#include "esp32_camera.h"
#include "mcp_server.h"
#include "display.h"
#include "board.h"
#include "system_info.h"

#include <esp_log.h>
#include <esp_heap_caps.h>
#include <img_converters.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <cstring>

#define TAG "Esp32Camera"

Esp32Camera::Esp32Camera(const camera_config_t& config) {
    preview_config_ = config;
    preview_config_.frame_size = FRAMESIZE_VGA;

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

    // 初始化预览图片的内存 (always 320x240 for the LCD)
    memset(&preview_image_, 0, sizeof(preview_image_));
    preview_image_.header.magic = LV_IMAGE_HEADER_MAGIC;
    preview_image_.header.cf = LV_COLOR_FORMAT_RGB565;
    preview_image_.header.flags = 0;
    preview_image_.header.w = 320;
    preview_image_.header.h = 240;

    preview_image_.header.stride = preview_image_.header.w * 2;
    preview_image_.data_size = preview_image_.header.w * preview_image_.header.h * 2;
    preview_image_.data = (uint8_t*)heap_caps_malloc(preview_image_.data_size, MALLOC_CAP_SPIRAM);
    if (preview_image_.data == nullptr) {
        ESP_LOGE(TAG, "Failed to allocate memory for preview image");
        return;
    }

    ApplyRegisterTuning();
}

bool Esp32Camera::ReconfigureToUXGA() {
    camera_config_t uxga_config = preview_config_;
    uxga_config.frame_size = FRAMESIZE_UXGA;
    uxga_config.fb_count = 1;

    ESP_LOGI(TAG, "Reconfiguring camera to UXGA 1600x1200");
    esp_err_t err = esp_camera_reconfigure(&uxga_config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "UXGA reconfigure failed: 0x%x, falling back to VGA", err);
        return false;
    }
    high_res_mode_ = true;
    ApplyRegisterTuning();
    return true;
}

void Esp32Camera::ReconfigureToVGA() {
    if (!high_res_mode_) return;

    if (fb_ != nullptr) {
        esp_camera_fb_return(fb_);
        fb_ = nullptr;
    }

    ESP_LOGI(TAG, "Reverting camera to VGA 640x480");
    esp_err_t err = esp_camera_reconfigure(&preview_config_);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "VGA reconfigure failed: 0x%x", err);
    }
    high_res_mode_ = false;
    ApplyRegisterTuning();
}

void Esp32Camera::ApplyRegisterTuning() {
    sensor_t *s = esp_camera_sensor_get();
    if (s == nullptr) return;

    // PCLK divider — the gc2145 driver only applies this for ESP32 (to keep
    // PCLK ≤ 15 MHz), but our board routing can't reliably handle the default
    // ~45 MHz PCLK either.  Without the divider the CAM peripheral samples
    // corrupted pixel data, producing colored blocks.
    s->set_reg(s, 0xfe, 0xff, 0x00);
    s->set_reg(s, 0xf8, 0x3f, 0x02);   // PLL divider m: default 3 → 2
    s->set_reg(s, 0xfa, 0x70, 0x20);   // divide_by:      default 0 → 2

    // ISP tuning — maximum quality: reduce denoise, push EE and YCP.
    s->set_reg(s, 0xfe, 0xff, 0x02);
    s->set_reg(s, 0x80, 0x03, 0x00);   // denoise level: 1→0 (less smoothing = sharper)
    s->set_reg(s, 0x82, 0xff, 0x10);   // denoise signal a thresh: 0x08→0x10
    s->set_reg(s, 0x83, 0xff, 0x10);   // denoise signal b thresh: 0x08→0x10
    s->set_reg(s, 0xdd, 0xff, 0x38);   // edge enhancement: 0x14 → 0x38
    s->set_reg(s, 0x97, 0xff, 0x80);   // edge effect:      0x64 → 0x80
    s->set_reg(s, 0xd2, 0xff, 0x55);   // saturation:       0x30 → 0x55
    s->set_reg(s, 0xd1, 0xff, 0x45);   // contrast:         0x30 → 0x45
    s->set_reg(s, 0xfe, 0xff, 0x00);
}

Esp32Camera::~Esp32Camera() {
    StopPreview();
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
    if (display == nullptr) return;

    DownscaleToPreview(fb_);
    display->SetPreviewImage(&preview_image_);
}

void Esp32Camera::DownscaleToPreview(const camera_fb_t* fb) {
    auto src = (const uint16_t*)fb->buf;
    auto dst = (uint16_t*)preview_image_.data;
    int src_w = fb->width;
    int src_h = fb->height;
    int dst_w = preview_image_.header.w;
    int dst_h = preview_image_.header.h;

    // 2x2 box filter (anti-aliased downscale) — camera data is big-endian
    // RGB565 from sensor, converted to little-endian for LVGL.
    for (int y = 0; y < dst_h; y++) {
        for (int x = 0; x < dst_w; x++) {
            int sx = x * src_w / dst_w;
            int sy = y * src_h / dst_h;

            uint32_t r = 0, g = 0, b = 0;
            int count = 0;
            for (int dy = 0; dy < 2 && sy + dy < src_h; dy++) {
                for (int dx = 0; dx < 2 && sx + dx < src_w; dx++) {
                    uint16_t p = __builtin_bswap16(src[(sy + dy) * src_w + (sx + dx)]);
                    r += (p >> 11) & 0x1f;
                    g += (p >> 5) & 0x3f;
                    b += p & 0x1f;
                    count++;
                }
            }
            dst[y * dst_w + x] = ((r / count) << 11) | ((g / count) << 5) | (b / count);
        }
    }
}

bool Esp32Camera::Capture() {
    if (encoder_thread_.joinable()) {
        encoder_thread_.join();
    }

    // Return to VGA if we're still in UXGA from a previous capture
    if (high_res_mode_) {
        ReconfigureToVGA();
    }

    // Reconfigure to UXGA for high-quality still capture (GC2145 native 1600x1200)
    if (!ReconfigureToUXGA()) {
        ESP_LOGW(TAG, "UXGA reconfigure failed, falling back to VGA capture");
    }

    // 3 warmup frames for AEC/AWB stabilization
    for (int i = 0; i < 3; i++) {
        if (fb_ != nullptr) {
            esp_camera_fb_return(fb_);
            fb_ = nullptr;
        }
        fb_ = esp_camera_fb_get();
        if (fb_ == nullptr) {
            ESP_LOGE(TAG, "Camera capture failed during warmup (frame %d)", i);
            ReconfigureToVGA();
            return false;
        }
    }

    ESP_LOGI(TAG, "Capture complete: %dx%d", fb_->width, fb_->height);
    UpdatePreview();
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
        ReconfigureToVGA();
        return "{\"success\": false, \"message\": \"Image explain URL or token is not set\"}";
    }

    // 创建局部的 JPEG 队列
    QueueHandle_t jpeg_queue = xQueueCreate(40, sizeof(JpegChunk));
    if (jpeg_queue == nullptr) {
        ESP_LOGE(TAG, "Failed to create JPEG queue");
        ReconfigureToVGA();
        return "{\"success\": false, \"message\": \"Failed to create JPEG queue\"}";
    }

    // 使用独立线程编码JPEG
    encoder_thread_ = std::thread([this, jpeg_queue]() {
        frame2jpg_cb(fb_, 90, [](void* arg, size_t index, const void* data, size_t len) -> unsigned int {
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
        ReconfigureToVGA();
        return "{\"success\": false, \"message\": \"Failed to connect to explain URL\"}";
    }

    {
        std::string question_field;
        question_field += "--" + boundary + "\r\n";
        question_field += "Content-Disposition: form-data; name=\"question\"\r\n";
        question_field += "\r\n";
        question_field += question + "\r\n";
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
        ReconfigureToVGA();
        return "{\"success\": false, \"message\": \"Failed to upload photo\"}";
    }

    std::string result = http->ReadAll();
    http->Close();

    ESP_LOGI(TAG, "Explain image size=%dx%d, compressed=%d",
        fb_->width, fb_->height, (int)total_sent);
    ESP_LOGI(TAG, "VLM response: %s", result.c_str());

    ReconfigureToVGA();

    return result;
}

void Esp32Camera::PreviewTaskFunc(void* arg) {
    auto* self = static_cast<Esp32Camera*>(arg);
    self->PreviewLoop();
    vTaskDelete(nullptr);
}

void Esp32Camera::PreviewLoop() {
    ESP_LOGI(TAG, "Camera preview started");
    auto display = Board::GetInstance().GetDisplay();

    while (preview_running_) {
        // Skip if photo capture is in progress
        if (high_res_mode_) {
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }

        camera_fb_t* fb = esp_camera_fb_get();
        if (fb == nullptr) {
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }

        // Downscale VGA → QVGA with 2x2 box filter for the LCD
        if (preview_image_.data != nullptr && preview_image_.data_size > 0) {
            DownscaleToPreview(fb);
            if (display) {
                display->SetPreviewImage(&preview_image_);
            }
        }

        esp_camera_fb_return(fb);

        // ~10 FPS
        vTaskDelay(pdMS_TO_TICKS(100));
    }

    ESP_LOGI(TAG, "Camera preview stopped");
}

void Esp32Camera::StartPreview() {
    if (preview_running_) return;
    preview_running_ = true;
    xTaskCreatePinnedToCore(PreviewTaskFunc, "cam_preview", 4096, this, 2, &preview_task_, 1);
}

void Esp32Camera::StopPreview() {
    if (!preview_running_) return;
    preview_running_ = false;
    // Task will exit on its own after seeing preview_running_ = false
    if (preview_task_ != nullptr) {
        // Give it time to finish
        vTaskDelay(pdMS_TO_TICKS(200));
        preview_task_ = nullptr;
    }
    // Hide preview, restore emotion display
    auto display = Board::GetInstance().GetDisplay();
    if (display) {
        display->SetPreviewImage(nullptr);
    }
}
