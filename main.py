import cv2 as cv
import numpy as np
from pathlib import Path
import os


# =========================
# 사용자 설정
# =========================

# 체커보드의 "내부 코너 수"
# 예: 10x7 칸짜리 체커보드라면 내부 코너는 (9, 6)
BOARD_PATTERN = (10, 7)

# 체커보드 한 칸의 실제 크기 (단위 자유: m / mm 중 하나로 통일만 하면 됨)
BOARD_CELL_SIZE = 0.025  # 예: 25 mm = 0.025 m

# 몇 프레임마다 한 번씩 검사할지
FRAME_STEP = 5

# 최대 사용할 샘플 수
MAX_SAMPLES = 40

# 왜곡 보정 이미지 저장 개수
SAVE_IMAGE_COUNT = 10

# 저장 폴더
OUTPUT_DIR = "undistorted_images"

# 코너 refinement 조건
SUBPIX_CRITERIA = (
    cv.TERM_CRITERIA_EPS + cv.TERM_CRITERIA_MAX_ITER,
    30,
    0.001,
)

# 체커보드 검출 플래그
CHESSBOARD_FLAGS = cv.CALIB_CB_ADAPTIVE_THRESH + cv.CALIB_CB_NORMALIZE_IMAGE

# 현재 폴더에서 찾을 영상 확장자
VIDEO_EXTENSIONS = ("*.mp4", "*.avi", "*.mov", "*.mkv", "*.MP4", "*.AVI", "*.MOV", "*.MKV")


# =========================
# 유틸
# =========================

def find_first_video_file():
    candidates = []
    for pattern in VIDEO_EXTENSIONS:
        candidates.extend(Path(".").glob(pattern))
    candidates = sorted([p for p in candidates if p.is_file()])

    if not candidates:
        raise FileNotFoundError(
            "현재 디렉토리에서 영상 파일을 찾지 못했습니다.\n"
            "이 스크립트와 같은 폴더에 mp4/avi/mov/mkv 영상을 넣고 다시 실행하세요."
        )
    return str(candidates[0])


def create_object_points(board_pattern, cell_size):
    cols, rows = board_pattern
    objp = np.zeros((rows * cols, 3), np.float32)
    objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2)
    objp *= cell_size
    return objp


def compute_reprojection_rmse(objpoints, imgpoints, rvecs, tvecs, K, dist):
    total_error_sq = 0.0
    total_points = 0

    for i in range(len(objpoints)):
        projected, _ = cv.projectPoints(objpoints[i], rvecs[i], tvecs[i], K, dist)
        err = cv.norm(imgpoints[i], projected, cv.NORM_L2)
        total_error_sq += err * err
        total_points += len(projected)

    if total_points == 0:
        return float("inf")
    return np.sqrt(total_error_sq / total_points)


def collect_points_from_video(video_file, board_pattern, cell_size):
    cap = cv.VideoCapture(video_file)
    if not cap.isOpened():
        raise RuntimeError(f"영상을 열 수 없습니다: {video_file}")

    objp = create_object_points(board_pattern, cell_size)
    objpoints = []
    imgpoints = []

    frame_idx = 0
    detected_count = 0
    total_frames = int(cap.get(cv.CAP_PROP_FRAME_COUNT))
    image_size = None

    print(f"[INFO] 입력 영상: {video_file}")
    print(f"[INFO] 추정 총 프레임 수: {total_frames}")
    print(f"[INFO] 체커보드 내부 코너: {board_pattern}")
    print(f"[INFO] 체커보드 한 칸 크기: {BOARD_CELL_SIZE}")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if image_size is None:
            image_size = (frame.shape[1], frame.shape[0])

        if frame_idx % FRAME_STEP != 0:
            frame_idx += 1
            continue

        gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY)
        found, corners = cv.findChessboardCorners(gray, board_pattern, CHESSBOARD_FLAGS)

        preview = frame.copy()
        status_text = f"Frame {frame_idx} | detected {detected_count}"

        if found:
            corners = cv.cornerSubPix(
                gray,
                corners,
                (11, 11),
                (-1, -1),
                SUBPIX_CRITERIA
            )

            objpoints.append(objp.copy())
            imgpoints.append(corners)
            detected_count += 1

            cv.drawChessboardCorners(preview, board_pattern, corners, found)
            status_text = f"Frame {frame_idx} | detected {detected_count}"

        cv.putText(preview, status_text, (10, 30),
                   cv.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv.LINE_AA)

        cv.imshow("Collecting Chessboard", preview)
        key = cv.waitKey(1) & 0xFF

        if key == 27:  # ESC
            print("[INFO] 사용자가 중단했습니다.")
            break

        if detected_count >= MAX_SAMPLES:
            print(f"[INFO] 최대 샘플 수 {MAX_SAMPLES}개를 모아 종료합니다.")
            break

        frame_idx += 1

    cap.release()
    cv.destroyAllWindows()

    if len(objpoints) == 0:
        raise RuntimeError(
            "체커보드를 한 번도 검출하지 못했습니다.\n"
            "1) BOARD_PATTERN이 맞는지\n"
            "2) 영상에서 체커보드가 충분히 크게 보이는지\n"
            "3) 초점이 맞고 너무 흔들리지 않았는지 확인하세요."
        )

    return objpoints, imgpoints, image_size


def calibrate_camera(objpoints, imgpoints, image_size):
    ret, K, dist, rvecs, tvecs = cv.calibrateCamera(
        objpoints,
        imgpoints,
        image_size,
        None,
        None
    )

    rmse = compute_reprojection_rmse(objpoints, imgpoints, rvecs, tvecs, K, dist)
    return ret, K, dist, rvecs, tvecs, rmse


def save_undistorted_images(video_file, K, dist, save_count, output_dir):
    os.makedirs(output_dir, exist_ok=True)

    cap = cv.VideoCapture(video_file)
    if not cap.isOpened():
        raise RuntimeError(f"영상을 다시 열 수 없습니다: {video_file}")

    width = int(cap.get(cv.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv.CAP_PROP_FRAME_HEIGHT))

    new_K, _ = cv.getOptimalNewCameraMatrix(K, dist, (width, height), 1, (width, height))

    map1, map2 = cv.initUndistortRectifyMap(
        K, dist, None, new_K, (width, height), cv.CV_16SC2
    )

    saved = 0
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # 저장 이미지는 영상 전체에서 고르게 뽑기 위해 FRAME_STEP 기반 사용
        if frame_idx % max(FRAME_STEP * 2, 10) == 0:
            undistorted = cv.remap(frame, map1, map2, interpolation=cv.INTER_LINEAR)

            out_path = os.path.join(output_dir, f"undistorted_{saved:02d}.jpg")
            cv.imwrite(out_path, undistorted)

            preview = undistorted.copy()
            cv.putText(preview, f"Saved: {saved+1}/{save_count}", (10, 30),
                       cv.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv.LINE_AA)
            cv.imshow("Undistorted Preview", preview)
            cv.waitKey(1)

            saved += 1
            if saved >= save_count:
                break

        frame_idx += 1

    cap.release()
    cv.destroyAllWindows()
    return saved


def main():
    video_file = find_first_video_file()

    objpoints, imgpoints, image_size = collect_points_from_video(
        video_file, BOARD_PATTERN, BOARD_CELL_SIZE
    )

    ret, K, dist, rvecs, tvecs, rmse = calibrate_camera(objpoints, imgpoints, image_size)

    print("\n========== Camera Calibration Result ==========")
    print(f"Used images : {len(objpoints)}")
    print(f"Image size  : {image_size[0]} x {image_size[1]}")
    print(f"RMSE        : {rmse:.6f}")

    print("\n[Camera Matrix K]")
    print(K)

    print("\n[Distortion Coefficients]")
    print(dist.ravel())

    print("\n[Important Values]")
    print(f"fx = {K[0, 0]}")
    print(f"fy = {K[1, 1]}")
    print(f"cx = {K[0, 2]}")
    print(f"cy = {K[1, 2]}")

    saved_count = save_undistorted_images(
        video_file, K, dist, SAVE_IMAGE_COUNT, OUTPUT_DIR
    )

if __name__ == "__main__":
    main()