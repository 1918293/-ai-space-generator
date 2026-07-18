from __future__ import annotations

import gradio as gr

from src.core import analyze_image, generate_design


CSS = """
.gradio-container { max-width: 1180px !important; }
.helper { color: var(--body-text-color-subdued); }
"""

with gr.Blocks(title="AI 空間生成器") as demo:
    gr.Markdown(
        "# AI 空間生成器\n"
        "從現況照片開始：讀取拍攝資訊、辨識影像、塗選修改區域，生成空間改造示意並比較下載。"
    )
    gr.Markdown(
        "**資料原則：** 此版本不建立帳號；照片只在目前工作階段處理。請勿把生成結果視為施工圖或精確 3D 模型。",
        elem_classes=["helper"],
    )

    with gr.Tabs():
        with gr.Tab("1　上傳與辨識"):
            with gr.Row():
                source_image = gr.Image(
                    label="現況照片",
                    type="pil",
                    sources=["upload", "webcam"],
                    height=520,
                )
                annotated_image = gr.Image(label="辨識結果", type="pil", height=520)
            analyze_button = gr.Button("分析照片", variant="primary")
            analysis_text = gr.Textbox(label="分析與拍攝資訊", lines=8, interactive=False)
            analyze_button.click(
                analyze_image,
                inputs=source_image,
                outputs=[analysis_text, annotated_image],
            )

        with gr.Tab("2　選取與生成"):
            gr.Markdown("先把照片載入編輯器，再用筆刷塗選需要移除、替換或更換材質的區域。")
            load_editor_button = gr.Button("載入照片到編輯器")
            editor = gr.ImageEditor(
                label="塗選修改區域",
                type="pil",
                sources=["upload"],
                height=560,
                brush=gr.Brush(colors=["#ff2d55"], color_mode="fixed", default_size=35),
                layers=gr.LayerOptions(allow_additional_layers=True),
            )
            load_editor_button.click(lambda image: image, inputs=source_image, outputs=editor)

            with gr.Row():
                action = gr.Dropdown(
                    ["移除物件", "替換／加入材質", "更換顏色"],
                    value="替換／加入材質",
                    label="修改方式",
                )
                material = gr.Dropdown(
                    ["木材", "石材／大理石", "混凝土", "塗料"],
                    value="木材",
                    label="材質",
                )
                colour = gr.ColorPicker(value="#d8d1c4", label="顏色")
            prompt = gr.Textbox(
                label="設計要求",
                placeholder="例如：保留原有牆體與窗戶，將選取的地板改成淺色橡木，光線自然。",
                lines=3,
            )
            with gr.Row():
                strength = gr.Slider(0.1, 1.0, value=0.85, step=0.05, label="修改強度")
                use_remote_ai = gr.Checkbox(
                    value=False,
                    label="使用遠端 AI（需要部署者設定 HF_TOKEN）",
                )
            generate_button = gr.Button("生成空間示意", variant="primary")
            status = gr.Textbox(label="狀態", interactive=False)
            with gr.Row():
                result_image = gr.Image(label="生成結果", type="pil", height=500)
                comparison = gr.ImageSlider(label="修改前／後比較", height=500)
            download = gr.File(label="下載 PNG")

            generate_button.click(
                generate_design,
                inputs=[editor, action, material, colour, prompt, strength, use_remote_ai],
                outputs=[result_image, comparison, status, download],
            )

        with gr.Tab("3　範圍與限制"):
            gr.Markdown(
                """
### 已實作
- 照片上傳與手機拍攝
- EXIF 日期、時間、裝置與 GPS 讀取（照片含資料時）
- 離線影像特徵與主要色彩分析
- 可選的 Hugging Face 物件偵測
- 筆刷區域選取
- 物件移除、材質與顏色的本機示意
- 可選的遠端 image-to-image 生成
- 未選取區域保留、前後比較與 PNG 下載

### 後續模組
- 室內專用語意分割
- 更精確的局部 inpainting
- 2D 平面圖解析
- 概念性 3D 空間與多視角一致生成
- Web 3D／glTF 輸出

### 限制
生成結果是設計討論用的概念示意，不能替代 CAD、BIM、測量、施工圖、結構判斷或專業簽證。
                """
            )

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=2, max_size=12).launch(css=CSS)
