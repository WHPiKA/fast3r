import os, glob
import numpy as np
from einops import rearrange
import torch
import open3d as o3d
from fast3r.dust3r.utils.image import load_images
from fast3r.dust3r.inference_multiview import inference
from fast3r.models.fast3r import Fast3R
from fast3r.models.multiview_dust3r_module import MultiViewDUSt3RLitModule

# --- Setup ---
# Load the model from Hugging Face
model = Fast3R.from_pretrained("Fast3R_ViT_Large_512")  # If you have networking issues, try pre-download the HF checkpoint dir and change the path here to a local directory
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model = model.to(device)

# Create a lightweight lightning module wrapper for the model.
# This provides functions to estimate camera poses, evaluate 3D reconstruction, etc.
lit_module = MultiViewDUSt3RLitModule.load_for_inference(model)

# Set model to evaluation mode
model.eval()
lit_module.eval()

# --- Load Images ---
# Provide a list of image file paths. Images can come from different cameras and aspect ratios.
filelist = glob.glob('/mnt/workspace/projects/fast3r/demo_examples/gnome/*.png')
images = load_images(filelist, size=512, verbose=True)

# --- Run Inference ---
# The inference function returns a dictionary with predictions and view information.
output_dict, profiling_info = inference(
    images,
    model,
    device,
    dtype=torch.float32,  # or use torch.bfloat16 if supported
    verbose=True,
    profiling=True,
)

# --- Estimate Camera Poses ---
# This step estimates the camera-to-world (c2w) poses for each view using PnP.
poses_c2w_batch, estimated_focals = MultiViewDUSt3RLitModule.estimate_camera_poses(
    output_dict['preds'],
    niter_PnP=100,
    focal_length_estimation_method='first_view_from_global_head'
)
# poses_c2w_batch is a list; the first element contains the estimated poses for each view.
camera_poses = poses_c2w_batch[0]

# Print camera poses for all views.
for view_idx, pose in enumerate(camera_poses):
    print(f"Camera Pose for view {view_idx}:")
    print(pose)  # np.array of shape (4, 4), the camera-to-world transformation matrix

# --- Extract 3D Point Clouds for Each View ---
# Each element in output_dict['preds'] corresponds to a view's point map.
all_points = []
for view_idx, pred in enumerate(output_dict['preds']):
    point_cloud = pred['pts3d_in_other_view'].cpu().numpy()
    print(f"Point Cloud Shape for view {view_idx}: {point_cloud.shape}")  # shape: (1, 368, 512, 3), i.e., (1, Height, Width, XYZ)
    all_points.append(point_cloud)

masked_points = []
for image, point_cloud in zip(images, all_points):
    img = image['img'].cpu().numpy()
    img = rearrange(img, 'b c h w -> b h w c')
    img = img * 0.5 + 0.5
    colored_points = np.concatenate([point_cloud, img], axis=-1)
    mask = image['mask'].cpu().numpy().astype(bool)
    mask = mask[:, 0]
    print(f"Mask Shape: {mask.shape}")
    colored_points = colored_points[mask].reshape(-1, 6)
    masked_points.append(colored_points)
    # pcd = o3d.geometry.PointCloud()
    # pcd.points = o3d.utility.Vector3dVector(colored_points[:, :3])
    # pcd.colors = o3d.utility.Vector3dVector(colored_points[:, 3:])

# --- Combine All Points into a Single Point Cloud ---
combined_points = np.concatenate(masked_points, axis=0)

# --- Create an Open3D Point Cloud Object ---
pcd = o3d.geometry.PointCloud()
pcd.points = o3d.utility.Vector3dVector(combined_points[:, :3])
pcd.colors = o3d.utility.Vector3dVector(combined_points[:, 3:])

# --- Save the Point Cloud to a PCD File ---
o3d.io.write_point_cloud("combined_point_cloud.ply", pcd)

print("Point cloud saved as 'combined_point_cloud.ply'.")
