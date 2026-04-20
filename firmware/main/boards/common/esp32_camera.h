#ifndef ESP32_CAMERA_H
#define ESP32_CAMERA_H

#include <esp_camera.h>
#include <lvgl.h>
#include <thread>
#include <memory>

#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

#include "camera.h"

struct JpegChunk {
    uint8_t* data;
    size_t len;
};

class Esp32Camera : public Camera {
private:
    camera_fb_t* fb_ = nullptr;
    lv_img_dsc_t preview_image_;
    std::string explain_url_;
    std::string explain_token_;
    std::thread encoder_thread_;

    // 图像质量检测
    int last_brightness_ = 0;
    int last_variance_ = 0;

    static constexpr int kMinWarmupFrames = 8;
    static constexpr int kMaxQualityRetries = 2;
    static constexpr int kRetryExtraFrames = 4;
    static constexpr int kMinBrightness = 30;
    static constexpr int kMaxBrightness = 240;
    static constexpr int kMinVariance = 100;

    void UpdatePreview();
    int CalculateMeanBrightness();
    int CalculateBrightnessVariance(int mean);
    bool IsFrameAcceptable();

public:
    Esp32Camera(const camera_config_t& config);
    ~Esp32Camera();

    virtual void SetExplainUrl(const std::string& url, const std::string& token);
    virtual bool Capture();
    virtual bool SetHMirror(bool enabled) override;
    virtual bool SetVFlip(bool enabled) override;
    virtual std::string Explain(const std::string& question);

    int GetLastBrightness() const { return last_brightness_; }
    int GetLastVariance() const { return last_variance_; }
};

#endif // ESP32_CAMERA_H
