# 261017 과제: 조명 오염된 이미지에서 농구공 영역 검출

LAB 색공간과 CLAHE로 만든 농구공 마스크에 Morphological Operation 중 Opening, Closing을 적용하고, Contour Detection으로 농구공 영역과 외곽선을 검출하여 원본 이미지와 겹친 이미지를 출력하는 과제를 수행했습니다.

## 파일 구조 (필수)

- assignment02.py: 메인 실행 파일입니다.
- 입력 이미지 소스: 저장소 공용 폴더의 `src/images/basketball_crop.jpg`


## 실행 방식 (필수)

### 실행 명령

저장소 루트에서 아래 명령어 실행

```bash
python 261017/assignment02.py
```

### 실행 결과

plt.show() 메서드를 통해 Matplotlib 윈도우에 농구공 이미지와 검출된 농구공 외곽선이 겹쳐진 이미지가 출력됩니다.

### 처리 과정

1. `cv2.imread`로 이미지를 불러옵니다(BGR 색공간).
2. LAB 색공간으로 변환하고 L, a, b 채널을 분리합니다.
3. L 채널에만 CLAHE(`clipLimit=3.0`, `tileGridSize=(8, 8)`)를 적용해 밝기 대비를 높인 강화된 이미지를 생성하고, a, b 채널과 다시 합칩니다.
4. 강화된 LAB 이미지에서 `cv2.inRange()`로 농구공 색 범위([70, 130, 70] ~ [180, 230, 185])의 Binary mask를 만듭니다.
5. 5×5 원형 커널(`MORPH_ELLIPSE`)로 Opening 5회를 적용해 배경 노이즈를 지우고, 그 결과에 Closing 4회를 적용해 공 내부의 빈 곳을 메웁니다.
6. `cv2.findContours(RETR_EXTERNAL, CHAIN_APPROX_SIMPLE)`로 바깥쪽 외곽선을 검출합니다.
7. 마스크를 원본 위에 반투명 초록색으로 겹치고, 외곽선을 그려 표시합니다.

## 기타 참고 사항

### Morphological Operation 반복 횟수(iteration) 파라미터 결정 과정

수업에서 활용된 `interact()` 코드를 응용하여, 적절한 iteration 파라미터 값을 탐색하는 데에 아래 코드를 작성하여 활용했습니다.

```python
def update_morph(opening_iterations=5, closing_iterations=4):
    ball_mask_opened = cv2.morphologyEx(mask_enhanced, cv2.MORPH_OPEN, kernel, iterations=opening_iterations)
    ball_mask_closed = cv2.morphologyEx(ball_mask_opened, cv2.MORPH_CLOSE, kernel, iterations=closing_iterations)

    plt.figure(figsize=(12, 8))
    plt.subplot(2,2,1), plt.imshow(basketball), plt.title("Original"), plt.axis("off")
    plt.subplot(2,2,3), plt.imshow(ball_mask_opened), plt.title(f"opening-iteration {opening_iterations}"), plt.axis("off")
    plt.subplot(2,2,4), plt.imshow(ball_mask_closed), plt.title(f"-> closing-iteration {closing_iterations}"), plt.axis("off")

    plt.show()

interact(
    update_morph,
    opening_iterations=(1,10,1),
    closing_iterations=(1,10,1)
)
```

Opening 반복 5회, Closing 반복 4회일 때 배경 노이즈가 사라져 외곽선이 농구공 하나만 남는 것을 확인하였고, 이 파라미터 값을 `assignment02.py` 파일 코드에 적용하였습니다.

### 작업 중 겪은 문제

- 이미지 경로 문제
처음에는 '../src/images/basketball_crop.jpg'처럼 상대 경로를 썼는데, 상대 경로는 파일 위치가 아니라 실행한 위치를 기준으로 해석되어 저장소 루트에서 실행하면 이미지를 읽지 못하는 문제가 있었습니다.  Path(__file__) 기준 경로로 바꿔 해결했습니다.
- `interact()`: ipywidgets의 슬라이더는 Jupyter 환경에서만 동작하는 것을 확인했습니다. 따라서 반복 횟수 탐색은 주피터 노트북에서 수행하고, .py 파일에는 탐색을 통해 찾은 적절한 값을 적용했습니다.