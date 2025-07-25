import torch
import os
import numpy as np
import requests

from PIL import ImageFont, ImageDraw, Image, ImageOps
from torchvision.transforms.functional import to_pil_image
import matplotlib.font_manager as fm

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from mypy.typeshed.stdlib._typeshed import SupportsDunderGT, SupportsDunderLT

def pil2tensor(images: Image.Image | list[Image.Image]) -> torch.Tensor:
    """Converts a PIL Image or a list of PIL Images to a tensor."""

    def single_pil2tensor(image: Image.Image) -> torch.Tensor:
        np_image = np.array(image).astype(np.float32) / 255.0
        if np_image.ndim == 2:  # Grayscale
            return torch.from_numpy(np_image).unsqueeze(0)  # (1, H, W)
        else:  # RGB or RGBA
            return torch.from_numpy(np_image).unsqueeze(0)  # (1, H, W, C)

    if isinstance(images, Image.Image):
        return single_pil2tensor(images)
    else:
        return torch.cat([single_pil2tensor(img) for img in images], dim=0)




#Code from https://github.com/dzqdzq/ComfyUI-crop-alpha
class SR_AlphaCropAndPositionImage:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "maintain_aspect": (["True", "False"], {"default": "True"}),
                "left_padding": ("INT", {"default": 0, "min": 0, "max": 1024, "step": 8}),
                "top_padding": ("INT", {"default": 0, "min": 0, "max": 1024, "step": 8}),
                "right_padding": ("INT", {"default": 0, "min": 0, "max": 1024, "step": 8}),
                "bottom_padding": ("INT", {"default": 0, "min": 0, "max": 1024, "step": 8}),
            }
        }

    RETURN_TYPES = ("IMAGE", "INT", "INT")
    RETURN_NAMES = ("image", "width", "height")

    FUNCTION = "crop"
    CATEGORY = "Showrunner Nodes"

    def crop(self, image, maintain_aspect, left_padding: int = 0, right_padding: int = 0, top_padding: int = 0, bottom_padding: int = 0):
        cropped_images = []
        cropped_masks = []

        for img in image:
            alpha = img[..., 3]

            height = img.shape[0]
            width = img.shape[1]
            mask = (alpha > 0.01)

            rows = torch.any(mask, dim=1)
            cols = torch.any(mask, dim=0)

            ymin, ymax = self._find_boundary(rows)
            xmin, xmax = self._find_boundary(cols)

            if ymin is None or xmin is None:
                cropped_images.append(img)
                cropped_masks.append(torch.zeros_like(alpha))
                continue

            cropped = img[ymin:ymax, xmin:xmax, :4]
            cropped_mask = alpha[ymin:ymax, xmin:xmax]

            # Apply padding to the cropped image
            padded_height = (ymax - ymin) + top_padding + bottom_padding
            padded_width = (xmax - xmin) + left_padding + right_padding

            if maintain_aspect == "True":
                if padded_height > padded_width:
                    pad = (padded_height - padded_width) // 2
                    left_padding += pad
                    right_padding += pad
                    padded_width = padded_height
                else:
                    pad = (padded_width - padded_height) // 2
                    top_padding += pad
                    bottom_padding += pad
                    padded_height = padded_width

            padded_image = torch.zeros((padded_height, padded_width, 4), dtype=img.dtype)
            padded_image[top_padding:top_padding + (ymax - ymin), left_padding:left_padding + (xmax - xmin), :] = cropped

            padded_mask = torch.zeros((padded_height, padded_width), dtype=alpha.dtype)
            padded_mask[top_padding:top_padding + (ymax - ymin), left_padding:left_padding + (xmax - xmin)] = cropped_mask

            cropped_images.append(padded_image)
            cropped_masks.append(padded_mask)

        return cropped_images, cropped_masks, padded_width, padded_height
    
    def _find_boundary(self, arr):
        nz = torch.nonzero(arr)
        if nz.numel() == 0:
            return (None, None)
        return (nz[0].item(), nz[-1].item() + 1)


class SR_ShrinkImage:
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(s):
        resize_algorithms = {
            "NEAREST": Image.NEAREST,
            "BILINEAR": Image.BILINEAR,
            "BICUBIC": Image.BICUBIC,
            "LANCZOS": Image.LANCZOS
        }
        return {
            "required": {
                "image": ("IMAGE",),
                "mode": (["scale", "pixels"], {"default": "scale"}),
                "resize_algorithm": (list(resize_algorithms.keys()), {"default": "LANCZOS"}),
                "maintain_aspect": (["True", "False"], {"default": "True"})
            },
            "optional": {
                "scale": ("FLOAT", {"default": 0.5, "min": 0.01, "max": 1.0, "step": 0.01}),
                "width": ("FLOAT", {"default": 100, "min": 2, "max": 10000, "step": 1}),
                "height": ("FLOAT", {"default": 100, "min": 2, "max": 10000, "step": 1})
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "shrink_image"
    CATEGORY = "Showrunner Nodes"

    def calculate_scale(self, img, mode, maintain_aspect, scale=None, width=None, height=None):
        if mode == "scale":
            return scale
        else:
            img_width, img_height = img.size
            if maintain_aspect == "True":
                aspect_ratio = img_width / img_height
                if width / height > aspect_ratio:
                    width = height * aspect_ratio
                else:
                    height = width / aspect_ratio
            scale_x = width / img_width
            scale_y = height / img_height
            return min(scale_x, scale_y)

    def shrink_image_with_scale(self, img, scale, algorithm):
        width, height = img.size
        new_width = int(width * scale)
        new_height = int(height * scale)
        return img.resize((new_width, new_height), algorithm)

    def shrink_image(self, image, mode, resize_algorithm, maintain_aspect, scale=None, width=None, height=None):
        resize_algorithms = {
            "NEAREST": Image.NEAREST,
            "BILINEAR": Image.BILINEAR,
            "BICUBIC": Image.BICUBIC,
            "LANCZOS": Image.LANCZOS
        }
        algorithm = resize_algorithms[resize_algorithm]

        output_images = []
        for img in image:
            img = to_pil_image(img.permute(2, 0, 1))
            scale = self.calculate_scale(img, mode, maintain_aspect, scale, width, height)
            resized_img = self.shrink_image_with_scale(img, scale, algorithm)
            resized_img_np = np.array(resized_img).astype(np.float32) / 255.0
            resized_img_np = torch.from_numpy(resized_img_np)
            output_images.append(resized_img_np)

        return (output_images,)


class SR_PadMask:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mask": ("MASK",),
                "top_padding": ("INT", {"default": 0, "min": 0}),
                "bottom_padding": ("INT", {"default": 0, "min": 0}),
                "left_padding": ("INT", {"default": 0, "min": 0}),
                "right_padding": ("INT", {"default": 0, "min": 0}),
            }
        }

    RETURN_TYPES = ("MASK",)
    RETURN_NAMES = ("Padded_Mask",)
    FUNCTION = "pad_mask"
    CATEGORY = "Showrunner Nodes"

    def pad_mask(self, mask, top_padding, bottom_padding, left_padding, right_padding):
        padded_mask = torch.zeros(
            (mask.shape[0] + top_padding + bottom_padding, 
             mask.shape[1] + left_padding + right_padding, 
             mask.shape[2]),
            dtype=mask.dtype
        )
        
        padded_mask[top_padding:top_padding + mask.shape[0], 
                    left_padding:left_padding + mask.shape[1]] = mask
        
        return (padded_mask,)
    

    
#Code From https://github.com/melMass/comfy_mtb
class SR_LoadImageFromUrl:
    """Load an image from the given URL"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "url": (
                    "STRING",
                    {
                        "default": ""
                    },
                ),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("IMAGE", "Image_Filename", "Image_Filename_No_Ext")
    FUNCTION = "load"
    CATEGORY = "Showrunner Nodes"

    def load(self, url):
        # Get the image from the url
        response = requests.get(url, stream=True)
        image = Image.open(response.raw)
        image = ImageOps.exif_transpose(image)

        # Extract filename from URL
        filename = os.path.basename(url)
        filename_no_ext = os.path.splitext(filename)[0]

        return (
            pil2tensor(image),
            filename,
            filename_no_ext
        )



class SR_AdjustBottomAlphaDistance:
    """Adjust the distance from the bottom of the image to the bottom of the alpha channel"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "max_distance": ("INT", {"default": 50, "min": 0, "max": 1000, "step": 1}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "adjust_distance"
    CATEGORY = "Showrunner Nodes"

    def adjust_distance(self, image, max_distance):
        # Accepts a list of images or a single image tensor
        adjusted_images = []

        # Normalize input to a list of images
        if isinstance(image, list):
            images = image
        elif hasattr(image, 'ndim') and image.ndim == 4:
            images = [img for img in image]
        else:
            images = [image]

        for img in images:
            if img.ndim == 3 and img.shape[2] >= 4:
                alpha_channel = img[..., 3]
                alpha_np = alpha_channel.detach().cpu().numpy()
                mask = alpha_np > 0.01
                if not mask.any():
                    # No alpha, leave unchanged
                    adjusted_images.append(img)
                    continue
                # Find the lowest row with nonzero alpha
                for y in range(mask.shape[0] - 1, -1, -1):
                    if mask[y, :].any():
                        bottom_alpha_row = y
                        break
                distance = mask.shape[0] - bottom_alpha_row - 1
                if distance > max_distance:
                    # Shift image up so only max_distance remains below alpha, fill top with alpha
                    orig_height = img.shape[0]
                    shift = distance - max_distance
                    # Crop off shift rows from the bottom
                    img_cropped = img[:orig_height - shift, :, :]
                    # Pad shift rows at the top with zeros (alpha)
                    pad = torch.zeros((shift, img.shape[1], img.shape[2]), dtype=img.dtype, device=img.device)
                    img_adjusted = torch.cat([pad, img_cropped], dim=0)
                    # Ensure output is same height as input
                    if img_adjusted.shape[0] > orig_height:
                        img_adjusted = img_adjusted[-orig_height:, :, :]
                    elif img_adjusted.shape[0] < orig_height:
                        pad2 = torch.zeros((orig_height - img_adjusted.shape[0], img.shape[1], img.shape[2]), dtype=img.dtype, device=img.device)
                        img_adjusted = torch.cat([pad2, img_adjusted], dim=0)
                    adjusted_images.append(img_adjusted)
                else:
                    adjusted_images.append(img)
            else:
                adjusted_images.append(img)
        return (adjusted_images,)

