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
    camera_config_t vga_config_;
    bool high_res_mode_ = false;
    bool preview_running_ = false;
    TaskHandle_t preview_task_ = nullptr;

    void UpdatePreview();
    bool ReconfigureToUXGA();
    void ReconfigureToVGA();
    void ApplyRegisterTuning();
    static void PreviewTaskFunc(void* arg);
    void PreviewLoop();

public:
    Esp32Camera(const camera_config_t& config);
    ~Esp32Camera();

    virtual void SetExplainUrl(const std::string& url, const std::string& token);
    virtual bool Capture();
    virtual bool SetHMirror(bool enabled) override;
    virtual bool SetVFlip(bool enabled) override;
    virtual std::string Explain(const std::string& question);

    void StartPreview();
    void StopPreview();
    bool IsPreviewRunning() const { return preview_running_; }
};

#endif // ESP32_CAMERA_H
