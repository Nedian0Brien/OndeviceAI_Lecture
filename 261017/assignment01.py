import cv2
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

basketball = cv2.imread(str(ROOT / "src" / "images" / "basketball_crop.jpg"))

lower_basketball_lab = np.array([70, 130, 70])
upper_basketball_lab = np.array([180, 230, 185])

# LAB 색공간으로 변환
basketball_rgb = cv2.cvtColor(basketball, cv2.COLOR_BGR2RGB)
basketball_lab = cv2.cvtColor(basketball_rgb, cv2.COLOR_RGB2LAB)

# 각 채널 분리
l, a, b = cv2.split(basketball_lab)

# CLAHE 생성 (clipLimit은 3.0, tileGridSize는 8x8로 설정)
clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))

# L 채널에 CLAHE 적용 후 강화된 이미지 생성
l_clahe_enhanced = clahe.apply(l)
basketball_enhanced_lab = cv2.merge((l_clahe_enhanced, a, b))

# 강화된 이미지로 binary mask 생성
mask_enhanced = cv2.inRange(basketball_enhanced_lab, lower_basketball_lab, upper_basketball_lab)

# TODO: Morphological operation을 적용하여 최종 binary mask 생성
# TODO: Binary mask로 외곽선 검출
# TODO: 최종 농구공 영역 및 외곽선 선택

# 적절한 Morphological operation iteration 최적값 탐색 -> 주피터 노트북에서 실행
# def update_morph(opening_iterations=5, closing_iterations=4):
#     ball_mask_opened = cv2.morphologyEx(mask_enhanced, cv2.MORPH_OPEN, kernel, iterations=opening_iterations)
#     ball_mask_closed = cv2.morphologyEx(ball_mask_opened, cv2.MORPH_CLOSE, kernel, iterations=closing_iterations)

#     plt.figure(figsize=(12, 8))
#     plt.subplot(2,2,1), plt.imshow(basketball), plt.title("Original"), plt.axis("off")
#     plt.subplot(2,2,3), plt.imshow(ball_mask_opened), plt.title(f"opening-iteration {opening_iterations}"), plt.axis("off")
#     plt.subplot(2,2,4), plt.imshow(ball_mask_closed), plt.title(f"-> closing-iteration {closing_iterations}"), plt.axis("off")

#     plt.show()

# interact(
#     update_morph,
#     opening_iterations=(1,10,1),
#     closing_iterations=(1,10,1)
# )

# opening_iteration=5, closingd_iteration=4에서 최적인 것 같음

kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))  # 5x5 원형 커널: 각 픽셀 주변을 원 모양 범위로 보고 연산 -> 결과 영역의 모서리가 둥글게 나옴


# 찾은 값 적용
ball_mask_opened = cv2.morphologyEx(mask_enhanced, cv2.MORPH_OPEN, kernel, iterations=5)
ball_mask_closed = cv2.morphologyEx(ball_mask_opened, cv2.MORPH_CLOSE, kernel, iterations=4)

final_ball_mask = ball_mask_closed
final_img = basketball.copy()

contours, _ = cv2.findContours(
    final_ball_mask, # binary mask
    cv2.RETR_EXTERNAL,
    cv2.CHAIN_APPROX_SIMPLE
)

if len(contours) > 0:
    # Binary mask를 반투명 초록색으로 overlay
    mask_color = np.zeros_like(final_img)
    mask_color[:, :, 1] = final_ball_mask
    alpha = 0.35
    final_img = cv2.addWeighted(final_img, 1.0, mask_color, alpha, 0)

    # Contour 그리기 (초록색 외곽선)
    cv2.drawContours(final_img, contours, -1, (0,255,0), 2)


plt.imshow(final_img)
plt.axis("off");
plt.show()
