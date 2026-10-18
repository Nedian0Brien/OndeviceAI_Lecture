import cv2
import numpy as np
from numpy.linalg import inv

pipeline = (
    "v4l2src device=/dev/video0 ! "
    "video/x-raw,format=YUY2,width=640,height=480,framerate=30/1 ! "
    "videoconvert ! video/x-raw,format=BGR ! "
    "queue leaky=downstream max-size-buffers=1 "
    "max-size-bytes=0 max-size-time=0 ! "
    "appsink drop=true max-buffers=1 sync=false"
)

cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)

def KalmanFilter(mu_prev, sigma_prev, z):
    mu_bar = A_t.dot(mu_prev)
    sigma_bar = A_t.dot(sigma_prev).dot(A_t.transpose()) + R_t
    if z is None:
        return mu_bar, sigma_bar
    else:
        K_t = sigma_bar.dot(C_t.transpose()).dot(inv(C_t.dot(sigma_bar).dot(C_t.transpose()) + Q_t))
        mu = mu_bar + K_t.dot(z - C_t.dot(mu_bar))
        sigma = (np.identity(2) - K_t.dot(C_t)).dot(sigma_bar)
        return mu, sigma


# Kalman filter 변수 정의
A_t = np.array([[1, 1], [0, 1]])
G = np.array([[0.5], [1]])
R_t = G.dot(G.transpose())
C_t = np.array([[1, 0]])
Q_t = np.array([[1]])
mu_t = np.array([[0, 0], [0, 0]])
sigma_t = np.array([[0, 0], [0, 0]])


# TODO: 배운 기술(이미지 전처리, 특징 추출)을 활용하여 실시간으로 객체를 검출
# TODO: 각 프레임 이미지를 전처리
# TODO: Color Segmentation으로 Binary Mask 생성
# TODO: Morphological Operation을 적용하여 객체 영역 추출
# TODO: 객체의 중심과 반지름을 구해 프레임 위에 overlay

# 초록 LAB lower/upper range
green_lower = np.array([30, 60, 90], dtype=np.uint8)
green_upper = np.array([230, 115, 180], dtype=np.uint8)

kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))  # 5x5 원형 커널

# 객체 최초 검출 여부 확인용 boolean
found = False

while True:
    ret, frame = cap.read()

    if not ret:
        break
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break


    # 색공간 변환
    frame_lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)

    # 각 채널 분리    # if len(contours) > 0:
    #     # Binary mask를 반투명 초록색으로 overlay
    #     mask_color = np.zeros_like(final_img)
    #     mask_color[:, :, 1] = final_ball_mask
    #     alpha = 0.35
    #     final_img = cv2.addWeighted(final_img, 1.0, mask_color, alpha, 0)

    #     # Contour 그리기 (초록색 외곽선) - 가장 큰 외곽선만 선택
    #     largest_contour = max(contours, key=cv2.contourArea)
    #     cv2.drawContours(final_img, [largest_contour], -1, (0,255,0), 2)
    l, a, b = cv2.split(frame_lab)

    # CLAHE 적용 후 강화된 이미지 생성
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))

    # L 채널에 CLAHE 적용 후 강화된 이미지 생성
    l_clahe_enhanced = clahe.apply(l)
    frame_enhanced_lab = cv2.merge((l_clahe_enhanced, a, b))

    # 강화된 이미지로 binary mask 생성
    mask_green_enhanced = cv2.inRange(frame_enhanced_lab, green_lower, green_upper)

    # Morphological Operation을 적용
    mask_green_opened = cv2.morphologyEx(mask_green_enhanced, cv2.MORPH_OPEN, kernel, iterations=4)
    mask_green_closed = cv2.morphologyEx(mask_green_opened, cv2.MORPH_CLOSE, kernel, iterations=4)

    final_ball_mask = mask_green_closed
    # imshow 및 오버레이 그리기에는 BGR 이미지를 사용
    final_img_enhanced = frame.copy()

    contours, _ = cv2.findContours(
        final_ball_mask, # binary mask
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    if len(contours) > 0:
        # 가장 큰 contour
        largest_contour = max(contours, key=cv2.contourArea)

        # 가장 큰 contour만 채운 binary mask 생성
        largest_mask = np.zeros_like(final_ball_mask)
        cv2.drawContours(
            largest_mask,
            [largest_contour],
            -1,
            255,
            thickness=cv2.FILLED
        )

        # 가장 큰 contour만 채운 binary mask를 반투명 빨간색으로 overlay
        mask_color = np.zeros_like(final_img_enhanced)
        mask_color[:, :, 2] = largest_mask
        alpha = 0.15
        final_img_enhanced = cv2.addWeighted(final_img_enhanced, 1.0, mask_color, alpha, 0)

        # Contour 그리기 (빨간색 외곽선)
        cv2.drawContours(final_img_enhanced, [largest_contour], -1, (0,0,255), 2)

        # 최소 외접원 중심 및 반지름
        (x, y), radius = cv2.minEnclosingCircle(largest_contour)
        center_circle = (int(x), int(y))
        radius = int(radius)

        # 파란색 최소 외접원
        cv2.circle(final_img_enhanced, center_circle, radius, (255,0,0), 3)
        cv2.circle(final_img_enhanced, center_circle, 5, (255,0,0), -1)

        # Moments를 통해 무게중심 (Centroid) 산출
        M = cv2.moments(largest_contour)

        if M["m00"] != 0:
            cX = int(M["m10"] / M["m00"])
            cY = int(M["m01"] / M["m00"])
        else:
            cX, cY = center_circle

        centroid = (cX, cY)

        # 노란색 무게중심 기반 원
        # cv2.circle(final_img_enhanced, centroid, radius, (0,255,255), 3)
        # cv2.circle(final_img_enhanced, centroid, 5, (0,255,255), -1)

        center = centroid
        if not found:
            # 최초 검출 위치로 초기화 (첫 행: x, y / 둘째 행: vx, vy)
            mu_t = np.array([[center[0], center[1]], [0.0, 0.0]])
            found = True
        else:
            # 측정값 사용 (visible) -> Prediction & Update
            mu_t, sigma_t = KalmanFilter(mu_t, sigma_t, np.array([list(center)]))

    elif found:
        # 측정값 미사용 (occluded) -> Prediction
        mu_t, sigma_t = KalmanFilter(mu_t, sigma_t, None)

    if found:
        x_bel, y_bel = mu_t[0][0], mu_t[0][1]
        # 검출이 끊기면 마지막으로 측정한 반지름 사용
        # 예측한 객체 위치에 노란 원 overlay
        cv2.circle(final_img_enhanced, (int(x_bel), int(y_bel)), int(radius), (0,255,255), 3)
        cv2.circle(final_img_enhanced, (int(x_bel), int(y_bel)), 5, (0,255,255), -1)



    cv2.imshow("VideoCapture with GStreamer", final_img_enhanced)

    
cap.release()
cv2.destroyAllWindows()







