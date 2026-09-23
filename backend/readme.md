Qwen-Image-3.0
Qwen-Image-3.0 is Alibaba’s image generation and editing model, with a focus on accurate text rendering and complex layouts.

What it’s good at
Accurate text. Renders small, legible text and handles native rendering across many languages and fonts.
Complex layouts. Generates posters, infographics, and multi-element compositions in a single pass.
Photographic detail. Produces sharp, detailed images.
Image editing. Pass a reference image to edit, restyle, or transform it.

Inputs
prompt — what to generate or how to edit the image.
image — optional reference image for editing and image-to-image.
aspect_ratio — output aspect ratio (1:1, 16:9, 9:16, 4:3, 3:4, 3:2, 2:3, 2:1, 1:2).
match_input_image — when editing, keep the input image’s aspect ratio.
negative_prompt — elements to avoid.
enable_prompt_expansion — automatically expand and optimize your prompt.
seed — set for reproducible results.

Pricing
$0.03 per output image.

replicate call:
import replicate

input = {
    "image": "https://replicate.delivery/pbxt/OejQrIXERvqS9kpygH9PfQZDOIdzkD6GKytAXxedNSyyRtej/9.png",
    "prompt": "The prune says \"And this, kids, is how you generate a video in less than 10 seconds\".",
    "prompt_upsampling": False
}

output = replicate.run(
    "prunaai/p-video",
    input=input
)

# To access the file URL:
print(output.url)
#=> "https://replicate.delivery/.../output.mp4"

# To write the file to disk:
with open("output.mp4", "wb") as file:
    file.write(output.read())
#=> output.mp4 written to disk