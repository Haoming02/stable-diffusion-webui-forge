from annotator.openpose import decode_json_as_poses, draw_poses
from annotator.openpose.animalpose import draw_animalposes
from lib_controlnet.logging import logger

import gradio as gr
import base64
import json


def parse_data_url(data_url: str) -> str:
    # Split the URL at the comma
    media_type, data = data_url.split(",", 1)

    # Check if the data is base64-encoded
    assert ";base64" in media_type

    # Decode the base64 data
    return base64.b64decode(data)


def encode_data_url(json_string: str) -> str:
    base64_encoded_json = base64.b64encode(json_string.encode("utf-8")).decode("utf-8")
    return f"data:application/json;base64,{base64_encoded_json}"


class OpenposeEditor:
    # Filename used when user click the download link
    download_file = "pose.json"

    def __init__(self) -> None:
        self.render_button = None
        self.pose_input = None
        self.download_link = None
        self.upload_link = None

    def render_edit(self):
        """Renders the buttons in preview image control button group."""
        # The hidden button to trigger a re-render of generated image.
        self.render_button = gr.Button(visible=False, elem_classes=["cnet-render-pose"])
        # The hidden element that stores the pose json for backend retrieval.
        # The front-end javascript will write the edited JSON data to the element.
        self.pose_input = gr.Textbox(visible=False, elem_classes=["cnet-pose-json"])
        # The button to download the pose json.
        self.download_link = gr.HTML(
            value=f'<a href="" download="{OpenposeEditor.download_file}">JSON</a>',
            visible=False,
            elem_classes=["cnet-download-pose"],
        )

    def render_upload(self):
        """Renders the button in input image control button group."""
        self.upload_link = gr.HTML(
            value='<label>Upload JSON</label><input type="file" accept=".json"/>',
            visible=False,
            elem_classes=["cnet-upload-pose"],
        )

    def register_callbacks(
        self,
        generated_image: gr.Image,
        use_preview_as_input: gr.Checkbox,
        model: gr.Dropdown,
    ):
        def render_pose(pose_url: str) -> tuple[dict]:
            json_string = parse_data_url(pose_url).decode("utf-8")
            poses, animals, height, width = decode_json_as_poses(
                json.loads(json_string)
            )
            logger.info("Preview as input is enabled.")
            return (
                # Generated image
                gr.update(
                    value=(
                        draw_poses(
                            poses,
                            height,
                            width,
                            draw_body=True,
                            draw_hand=True,
                            draw_face=True,
                        )
                        if poses
                        else draw_animalposes(animals, height, width)
                    ),
                    visible=True,
                ),
                # Use preview as input
                gr.update(value=True),
                # Self content
                *self.update(json_string),
            )

        self.render_button.click(
            fn=render_pose,
            inputs=[self.pose_input],
            outputs=[generated_image, use_preview_as_input, *self.outputs()],
        )

        def update_upload_link(model: str) -> dict:
            return gr.update(visible=("openpose" in model.lower()))

        model.change(fn=update_upload_link, inputs=[model], outputs=[self.upload_link])

    def outputs(self) -> list[gr.components.Component]:
        return [self.download_link]

    def update(self, json_string: str) -> list[dict]:
        """
        Called when there is a new JSON pose value generated by running
        preprocessor.

        Args:
            json_string: The new JSON string generated by preprocessor.

        Returns:
            An gr.update event.
        """

        hint = "Download the pose as .json file"
        html = f'<a href="{encode_data_url(json_string)}" download="{OpenposeEditor.download_file}" title="{hint}">JSON</a>'
        visible: bool = json_string != ""
        return [
            # Download link update
            gr.update(value=html, visible=visible),
        ]
