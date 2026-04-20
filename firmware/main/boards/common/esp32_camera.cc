#include "esp32_camera.h"
#include "mcp_server.h"
#include "display.h"
#include "board.h"
#include "system_info.h"

#include <esp_log.h>
#include <esp_heap_caps.h>
#include <img_converters.h>
#include <cstring>

#define TAG "Esp32Camera"

Esp32Camera::Esp32Camera(const camera_config_t& config) {
    // camera init
    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Camera init failed with error 0x%x", err);
        return;
    }

    sensor_t *s = esp_camera_sensor_get();
    if (s->id.PID == GC0308_PID) {
        s->set_hmirror(s, 0);
    }

    // 初始化预览图片的内存
    memset(&preview_image_, 0, sizeof(preview_image_));
    preview_image_.header.magic = LV_IMAGE_HEADER_MAGIC;
    preview_image_.header.cf = LV_COLOR_FORMAT_RGB565;
    preview_image_.header.flags = 0;

    switch (config.frame_size) {
        case FRAMESIZE_SVGA:
            preview_image_.header.w = 800;
            preview_image_.header.h = 600;
            break;
        case FRAMESIZE_VGA:
            preview_image_.header.w = 640;
            preview_image_.header.h = 480;
            break;
        case FRAMESIZE_QVGA:
            preview_image_.header.w = 320;
            preview_image_.header.h = 240;
            break;
        case FRAMESIZE_128X128:
            preview_image_.header.w = 128;
            preview_image_.header.h = 128;
            break;
        case FRAMESIZE_240X240:
            preview_image_.header.w = 240;
            preview_image_.header.h = 240;
            break;
        default:
            ESP_LOGE(TAG, "Unsupported frame size: %d, image preview will not be shown", config.frame_size);
            preview_image_.data_size = 0;
            preview_image_.data = nullptr;
            return;
    }

    preview_image_.header.stride = preview_image_.header.w * 2;
    preview_image_.data_size = preview_image_.header.w * preview_image_.header.h * 2;
    preview_image_.data = (uint8_t*)heap_caps_malloc(preview_image_.data_size, MALLOC_CAP_SPIRAM);
    if (preview_image_.data == nullptr) {
        ESP_LOGE(TAG, "Failed to allocate memory for preview image");
        return;
    }
}

Esp32Camera::~Esp32Camera() {
    if (fb_) {
        esp_camera_fb_return(fb_);
        fb_ = nullptr;
    }
    if (preview_image_.data) {
        heap_caps_free((void*)preview_image_.data);
        preview_image_.data = nullptr;
    }
    esp_camera_deinit();
}

void Esp32Camera::SetExplainUrl(const std::string& url, const std::string& token) {
    explain_url_ = url;
    explain_token_ = token;
}

void Esp32Camera::UpdatePreview() {
    if (preview_image_.data_size == 0 || preview_image_.data == nullptr || fb_ == nullptr) {
        return;
    }
    auto display = Board::GetInstance().GetDisplay();
    if (display != nullptr) {
        auto src = (uint16_t*)fb_->buf;
        auto dst = (uint16_t*)preview_image_.data;
        size_t pixel_count = fb_->len / 2;
        for (size_t i = 0; i < pixel_count; i++) {
            dst[i] = __builtin_bswap16(src[i]);
        }
        display->SetPreviewImage(&preview_image_);
    }
}

int Esp32Camera::CalculateMeanBrightness() {
    if (fb_ == nullptr) return 0;
    auto pixels = (uint16_t*)fb_->buf;
    size_t count = fb_->len / 2;
    // 每隔4个像素采样，提取绿色通道 (RGB565 bit[10:5])
    int64_t sum = 0;
    size_t sampled = 0;
    for (size_t i = 0; i < count; i += 4) {
        uint16_t green565 = (pixels[i] >> 5) & 0x3F;
        sum += green565;
        sampled++;
    }
    return (sampled > 0) ? (int)(sum / sampled) : 0;
}

int Esp32Camera::CalculateBrightnessVariance(int mean) {
    if (fb_ == nullptr) return 0;
    auto pixels = (uint16_t*)fb_->buf;
    size_t count = fb_->len / 2;
    int64_t sum_sq = 0;
    size_t sampled = 0;
    for (size_t i = 0; i < count; i += 4) {
        uint16_t green565 = (pixels[i] >> 5) & 0x3F;
        int diff = (int)green565 - mean;
        sum_sq += diff * diff;
        sampled++;
    }
    return (sampled > 0) ? (int)(sum_sq / sampled) : 0;
}

bool Esp32Camera::IsFrameAcceptable() {
    last_brightness_ = CalculateMeanBrightness();
    last_variance_ = CalculateBrightnessVariance(last_brightness_);
    ESP_LOGI(TAG, "Quality check: brightness=%d, variance=%d", last_brightness_, last_variance_);
    return last_brightness_ >= kMinBrightness &&
           last_brightness_ <= kMaxBrightness &&
           last_variance_ >= kMinVariance;
}

bool Esp32Camera::Capture() {
    if (encoder_thread_.joinable()) {
        encoder_thread_.join();
    }

    // Phase 1: 预热帧，让AE/AWB稳定
    for (int i = 0; i < kMinWarmupFrames; i++) {
        if (fb_ != nullptr) {
            esp_camera_fb_return(fb_);
        }
        fb_ = esp_camera_fb_get();
        if (fb_ == nullptr) {
            ESP_LOGE(TAG, "Camera capture failed during warmup (frame %d)", i);
            return false;
        }
        // 每帧更新预览，用户能看到机器人在"看"
        UpdatePreview();
    }

    // Phase 2: 质量检测，不合格则重试
    int retry_count = 0;
    while (retry_count < kMaxQualityRetries) {
        if (IsFrameAcceptable()) {
            break;
        }
        ESP_LOGW(TAG, "Frame quality poor (brightness=%d, variance=%d), retry %d/%d",
                 last_brightness_, last_variance_, retry_count + 1, kMaxQualityRetries);
        // 多抓几帧让曝光继续稳定
        for (int i = 0; i < kRetryExtraFrames; i++) {
            if (fb_ != nullptr) {
                esp_camera_fb_return(fb_);
            }
            fb_ = esp_camera_fb_get();
            if (fb_ == nullptr) {
                ESP_LOGE(TAG, "Camera capture failed during retry");
                return false;
            }
            UpdatePreview();
        }
        retry_count++;
    }

    ESP_LOGI(TAG, "Capture complete: brightness=%d, variance=%d, retries=%d",
             last_brightness_, last_variance_, retry_count);
    return true;
}

bool Esp32Camera::SetHMirror(bool enabled) {
    sensor_t *s = esp_camera_sensor_get();
    if (s == nullptr) {
        ESP_LOGE(TAG, "Failed to get camera sensor");
        return false;
    }

    esp_err_t err = s->set_hmirror(s, enabled);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to set horizontal mirror: %d", err);
        return false;
    }

    ESP_LOGI(TAG, "Camera horizontal mirror set to: %s", enabled ? "enabled" : "disabled");
    return true;
}

bool Esp32Camera::SetVFlip(bool enabled) {
    sensor_t *s = esp_camera_sensor_get();
    if (s == nullptr) {
        ESP_LOGE(TAG, "Failed to get camera sensor");
        return false;
    }

    esp_err_t err = s->set_vflip(s, enabled);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to set vertical flip: %d", err);
        return false;
    }

    ESP_LOGI(TAG, "Camera vertical flip set to: %s", enabled ? "enabled" : "disabled");
    return true;
}

std::string Esp32Camera::Explain(const std::string& question) {
    if (explain_url_.empty()) {
        return "{\"success\": false, \"message\": \"Image explain URL or token is not set\"}";
    }

    // 在问题中加入图像质量上下文，让VLLM了解拍摄条件
    std::string quality_context = "[图像质量: 亮度=" + std::to_string(last_brightness_) +
        ", 对比度=" + std::to_string(last_variance_) + "]";
    if (last_brightness_ < kMinBrightness + 20) {
        quality_context += " 注意：图像可能偏暗，请根据可见内容尽量回答。";
    }
    std::string full_question = quality_context + " " + question;

    // 创建局部的 JPEG 队列
    QueueHandle_t jpeg_queue = xQueueCreate(40, sizeof(JpegChunk));
    if (jpeg_queue == nullptr) {
        ESP_LOGE(TAG, "Failed to create JPEG queue");
        return "{\"success\": false, \"message\": \"Failed to create JPEG queue\"}";
    }

    // 使用独立线程编码JPEG
    encoder_thread_ = std::thread([this, jpeg_queue]() {
        frame2jpg_cb(fb_, 80, [](void* arg, size_t index, const void* data, size_t len) -> unsigned int {
            auto jpeg_queue = (QueueHandle_t)arg;
            JpegChunk chunk = {
                .data = (uint8_t*)heap_caps_aligned_alloc(16, len, MALLOC_CAP_SPIRAM),
                .len = len
            };
            memcpy(chunk.data, data, len);
            xQueueSend(jpeg_queue, &chunk, portMAX_DELAY);
            return len;
        }, jpeg_queue);
    });

    auto network = Board::GetInstance().GetNetwork();
    auto http = network->CreateHttp(3);
    std::string boundary = "----ESP32_CAMERA_BOUNDARY";

    http->SetHeader("Device-Id", SystemInfo::GetMacAddress().c_str());
    http->SetHeader("Client-Id", Board::GetInstance().GetUuid().c_str());
    if (!explain_token_.empty()) {
        http->SetHeader("Authorization", "Bearer " + explain_token_);
    }
    http->SetHeader("Content-Type", "multipart/form-data; boundary=" + boundary);
    http->SetHeader("Transfer-Encoding", "chunked");
    if (!http->Open("POST", explain_url_)) {
        ESP_LOGE(TAG, "Failed to connect to explain URL");
        encoder_thread_.join();
        JpegChunk chunk;
        while (xQueueReceive(jpeg_queue, &chunk, portMAX_DELAY) == pdPASS) {
            if (chunk.data != nullptr) {
                heap_caps_free(chunk.data);
            } else {
                break;
            }
        }
        vQueueDelete(jpeg_queue);
        return "{\"success\": false, \"message\": \"Failed to connect to explain URL\"}";
    }

    {
        std::string question_field;
        question_field += "--" + boundary + "\r\n";
        question_field += "Content-Disposition: form-data; name=\"question\"\r\n";
        question_field += "\r\n";
        question_field += full_question + "\r\n";
        http->Write(question_field.c_str(), question_field.size());
    }
    {
        std::string file_header;
        file_header += "--" + boundary + "\r\n";
        file_header += "Content-Disposition: form-data; name=\"file\"; filename=\"camera.jpg\"\r\n";
        file_header += "Content-Type: image/jpeg\r\n";
        file_header += "\r\n";
        http->Write(file_header.c_str(), file_header.size());
    }

    size_t total_sent = 0;
    while (true) {
        JpegChunk chunk;
        if (xQueueReceive(jpeg_queue, &chunk, portMAX_DELAY) != pdPASS) {
            ESP_LOGE(TAG, "Failed to receive JPEG chunk");
            break;
        }
        if (chunk.data == nullptr) {
            break;
        }
        http->Write((const char*)chunk.data, chunk.len);
        total_sent += chunk.len;
        heap_caps_free(chunk.data);
    }
    encoder_thread_.join();
    vQueueDelete(jpeg_queue);

    {
        std::string multipart_footer;
        multipart_footer += "\r\n--" + boundary + "--\r\n";
        http->Write(multipart_footer.c_str(), multipart_footer.size());
    }
    http->Write("", 0);

    if (http->GetStatusCode() != 200) {
        ESP_LOGE(TAG, "Failed to upload photo, status code: %d", http->GetStatusCode());
        return "{\"success\": false, \"message\": \"Failed to upload photo\"}";
    }

    std::string result = http->ReadAll();
    http->Close();

    size_t remain_stack_size = uxTaskGetStackHighWaterMark(nullptr);
    ESP_LOGI(TAG, "Explain image size=%dx%d, compressed size=%d, remain stack=%d, quality=[b=%d,v=%d]\n%s",
        fb_->width, fb_->height, total_sent, remain_stack_size, last_brightness_, last_variance_, result.c_str());
    return result;
}
