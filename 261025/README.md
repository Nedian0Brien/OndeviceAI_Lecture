# 261025 실습 과제

노트북03, 04 파일에 포함된 실습 과제를 수행했습니다. 
이미지에 잔상 효과를 추가하는 실습 코드를 CPU, GPU 버전으로 작성했으며, 기존 Convolution2D 및 MNIST 학습 코드를 변형하여 GPU를 활용하는 버전으로 구현했습니다. 또한 CIFAR-10 데이터셋 다운로드, GPU 학습, 그리고 모델 저장을 수행하였고, IoU 계산과 NMS 기반의 BBox 중복 제거를 수행하는 함수를 작성했습니다.

## 파일 구조 (필수)

- 03_DL-and-GPU.ipynb: 수업 노트북 03에서 과제에 필요한 코드 셀과 마크다운 셀을 옮긴 노트북입니다. GPU 가속(잔상 효과, Convolution2D), MNIST main_gpu() 함수, CIFAR-10 학습 + 모델 저장을 포함하고 있습니다.
- 04_DL-Object-Detection.ipynb: 수업 노트북 04에서 IoU와 NMS 관련 셀만 옮긴 노트북입니다.
- CIFAR10_CNN.pth: CIFAR-10으로 5 epoch 학습한 CNN 모델 파일입니다.

## 실행 방식 (필수)

03_DL-and-GPU.ipynb, 04_DL-Object-Detection.ipynb 노트북에 .venv 가상환경을 설정하고 코드 셀을 실행합니다.
- 원활한 실행을 위하여 이미지 파일 등 입력 호출(src 폴더)의 상대 경로 주소를 위 두 노트북 위치 기준으로 변경했습니다.

## 기타 참고 사항

과제별 내용과 결과 및 느낀 점, 궁금증

### 1. 이미지 잔상 효과 (NumPy CPU vs. CuPy GPU)

- CPU 기반 실행 함수와 GPU 기반 실행 함수의 속도 차이를 직접 체감해 볼 수 있었습니다. jetson에서는 600배 가량의 속도 차이가, 집에서 5080 GPU로 실습했을 때에는 2236배의 속도 차이가 났습니다.
```bash
# TODO: CPU/GPU 결과 비교 및 출력 - 5080 실행 시 결과

========== NumPy CPU ==========
Processing time: 4.7332 seconds

========== CuPy GPU ==========
CPU to GPU: 0.0790 seconds
GPU processing: 0.0021 seconds
GPU to CPU: 0.0007 seconds
Total GPU time: 0.0818 seconds

========== Comparison ==========
Compute-only speedup: 2236.77x
Including transfers: 57.84x
```

- 실습 장비로 사용하는 jetson nano와 5080 GPU의 컴퓨팅 성능 차이가 6배보다 훨씬 큼에도 불구하고, 실제 함수 실행 속도 차이는 6배 미만에서 그쳤습니다. 실행하는 함수의 연산(잔상 그리기) 부하가 크지 않은 만큼, 두 장비에서의 속도 차이는 컴퓨팅 성능 차이보다는 메모리 속도 및 대역폭 차이에서 발생(메모리 오버헤드가 속도 차이를 지배)하는 것이 아닌지 의심됩니다.

-> 생각해 본 결과 서로 다른 장비에서의 CPU/GPU 간 상대 속도 비교이기 때문에 수치를 1:1로 비교할 수 없다는 사실을 깨달았습니다. 위 문장은 실습을 수행하며 든 생각을 남기기 위해 존치했습니다.

- CPU 기반 함수에서는 픽셀 단위로 순회하며 연산하는 반면, GPU 기반 함수에서는 배열 단위로 잔상 픽셀값을 가져올 곳과 적용할 곳을 분리하여 적용해야 한다는 점에서, CPU 기반 코드를 설계할 때와 GPU 기반 코드를 설계할 때에는 사고방식을 달리해야 한다는 점을 느꼈습니다. 향후 보다 복잡한 태스크에서 CPU 기반 코드와 GPU 기반 코드의 동작 방식 차이를 좀더 살펴봐야겠다고 생각했습니다.


### 5. IoU와 NMS

```python
def calculate_iou(bbox_a, bbox_b, input_format="xywh"):
    if input_format == "xywh": # 실습 노트북에서 입력으로 xywh, xyxy 두 형식이 들어올 수 있어 아래쪽 실습에서의 코드 재사용을 위해 if문 분기 처리했습니다.
        xa, ya, wa, ha = bbox_a
        xb, yb, wb, hb = bbox_b

        xa_1, ya_1, xa_2, ya_2 = convert_bbox_coord(bbox_a)
        xb_1, yb_1, xb_2, yb_2 = convert_bbox_coord(bbox_b)

    elif input_format == "xyxy":
        xa_1, ya_1, xa_2, ya_2 = bbox_a
        xb_1, yb_1, xb_2, yb_2 = bbox_b

        wa = xa_2 - xa_1
        ha = ya_2 - ya_1
        wb = xb_2 - xb_1
        hb = yb_2 - yb_1
    else:
        print("형식이 올바르게 지정되지 않음")
        return None
        
    intersection_x1 = max(xa_1, xb_1)
    intersection_y1 = max(ya_1, yb_1)
    intersection_x2 = min(xa_2, xb_2)
    intersection_y2 = min(ya_2, yb_2)

    w = max(0, intersection_x2 - intersection_x1)
    h = max(0, intersection_y2 - intersection_y1)

    intersection_area = w * h
    union_area = wa * ha + wb * hb - intersection_area

    if union_area == 0:
        return 0

    # TODO: ZeroDivisionError 주의
    return intersection_area / union_area
```

위 함수를 IoU 계산을 위해 정의하였는데, 실습 노트북 아래쪽의 NMS를 통한 BBox 중복 제거 실습을 하며 `iou_threshold`값에 따라 BBox 중복을 제거하기 위해 해당 함수를 재사용하게 되었습니다.
재사용하는 과정에서 아래쪽 NMS 실습에서는 xywh 형식의 입력이 아닌 xyxy 형태의 입력이 들어온다는 것을 확인하여, 위와 같이 if문 분기를 통해 iou 계산에 필요한 width, height 값을 확보하도록 코드를 작성하였습니다.

## 드리고 싶은 말

항상 열정적으로 좋은 강의를 해 주셔서 감사드립니다. 컴퓨터비전, GPU 프로그래밍 등 새로운 분야에 대한 배움 뿐만 아니라, 학습에 대한 자세와 자신의 코드에 대한 책임감, 엔지니어로서 이해해야 하는 범위, 그리고 올바른 AI 활용법까지 많은 측면에서 배움을 얻고 있습니다.