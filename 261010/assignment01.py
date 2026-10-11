# %% [markdown]
# <h1 style="text-align: center;">Physical AI의 Vision-LLM 융합 시청각 멀티모달 시스템</h1>
# 
# <br><br>
# 
# <div style="text-align: right; color: gray; font-style: italic;">
# 김규래&emsp;<br>
# kkr.kyurae.kim@gmail.com&emsp;
# </div><br>
# 
# ---
# ---
# <br>

# %% [markdown]
# ## 1. 기초 Linux CLI 및 Python 문법
# 
# 본 세션에서는 Jetson 환경에서 개발을 진행하기에 앞서, 기초적인 Linux CLI와 Python 문법을 다룰 예정입니다.

# %% [markdown]
# ### A. Linux 기초 CLI
# 
# 다소 생소할 수 있는 Linux라는 운영 체제(OS)에서는 익숙한 그래픽 인터페이스(GUI)보다 CLI(Command Line Interface)를 활용하는 경우가 많아, 대부분의 작업을 터미널에서 명령어를 입력하는 방식으로 다양한 작업을 수행합니다.

# %% [markdown]
# CLI란?
# "사용자가 명령어(Command)를 입력하여 컴퓨터와 상호작용하는 방식"
# 
# * GUI (Graphical User Interface)
#   * 아이콘, 창, 마우스로 조작
# * CLI (Command Line Interface)
#   * 명령어 입력으로 조작

# %% [markdown]
# 터미널 vs. 셸
# * 터미널 (Terminal): 사용자가 명령어를 입력할 수 있는 창
# * 셸 (Shell): 입력받은 명령어를 해석하고 실행하는 프로그램 (예: bash)

# %% [markdown]
# #### $1)$ 터미널 프롬프트 (prompt) 이해

# %% [markdown]
# 터미널을 실행하면 보이는 문자열:
# 
# ```bash
# user_name@host_name:~/parent_directory/current_directory$
# ```
# 
# * user_name: 현재 로그인한 사용자 이름
# * host_name: 현재 접속한 컴퓨터(호스트) 이름
# * @: 사용자와 컴퓨터를 구분하는 표시
# * ~: 사용자의 home 디렉토리(폴더)
# * /: 폴더와 폴더를 구분하는 표시
# * parent_directory: 상위(부모) 디렉토리
# * current_directory: 현재 디렉토리
# * $: 일반 사용자 권한을 나타내는 표시 (#: 관리자 권한)

# %% [markdown]
# #### $2)$ 기본 터미널 명령어 종류

# %% [markdown]
# (a) `cd`: 디렉토리 이동 (change directory)
# 
# ```bash
# cd target_directory
# ```

# %% [markdown]
# (b) `cd`: 홈 디렉토리로 이동
# 
# ```bash
# cd
# ```

# %% [markdown]
# (c) `cd ..`: 상위 디렉토리로 이동
# 
# ```bash
# cd ..
# ```

# %% [markdown]
# (d) `pwd`: 현재 디렉토리 확인 (print working directory)
# 
# ```bash
# pwd
# ```

# %% [markdown]
# (e) `ls`: 현재 디렉토리 내 파일 목록 확인 (list)
# 
# ```bash
# ls
# ```

# %% [markdown]
# (f) `ls -a`: 현재 디렉토리 내 숨김 파일 포함 모든 파일 확인 (list -all)
# 
# ```bash
# ls -a
# ```

# %% [markdown]
# (g) `mkdir`: 현재 경로에 새로운 폴더 생성 (make directory)
# 
# ```bash
# mkdir folder_name
# ```

# %% [markdown]
# (h) `mkdir -p`: 현재 경로에 새로운 폴더 생성하며 상위 폴더까지 생성 (make directory -parent)
# 
# ```bash
# mkdir -p parent_folder_name/folder_name
# ```

# %% [markdown]
# (i) `touch`: 현재 경로에 새로운 파일 생성
# 
# ```bash
# touch file_name.txt
# ```

# %% [markdown]
# (j) `cp`: 파일 복사 (copy)
# 
# ```bash
# cp file_path/file_name copy_path/new_file_name
# ```

# %% [markdown]
# (k) `mv`: 파일 이동 (move)
# 
# ```bash
# mv file_path/file_name move_path/new_file_name
# ```

# %% [markdown]
# (l) `rm`: 파일 삭제 (remove)
# 
# ```bash
# rm file_path/file_name
# ```

# %% [markdown]
# (m) `rm -r`: 해당 폴더 및 모든 내부 파일 삭제 (remove -recursive)
# 
# ```bash
# rm -r folder_path
# ```

# %% [markdown]
# (n) rmdir: 빈 폴더 삭제 (remove directory)
# 
# ```bash
# rmdir empty_folder
# ```

# %% [markdown]
# (o) echo: 입력한 문자열이나 값을 터미널에 출력 혹은 파일에 저장
# 
# ```bash
# echo hi
# echo "hi"
# echo $USER
# echo "hi" > test.txt
# echo "hello" > test.txt
# echo "again" >> test.txt
# ```

# %% [markdown]
# (p) chmod: 파일/폴더의 권한 변경 (change mode)
# 
# ```bash
# chmod +x file_name
# chmod -x file_name
# chmod +w file_name
# chmod -w file_name
# chmod 444 file_name
# chmod 644 file_name
# ```

# %% [markdown]
# (q) nano: 터미널에서 GUI없이 사용하는 텍스트 편집기
# 
# ```bash
# nano file_name
# ```

# %% [markdown]
# (r) ping: 특정 컴퓨터(호스트)와 네트워크 연결 상태를 확인
# 
# ```bash
# ping 8.8.8.8
# ping <ip_address>
# ping <host_name>.local
# ```
# 
# IP 주소 확인법:
# 
# ```bash
# hostname -I
# ```

# %% [markdown]
# (s) ssh: 원격 컴퓨터(Jetson)에 접속
# 
# ```bash
# ssh <user_name>@<host_name>
# ssh <user_name>@<ip_address>
# ```

# %% [markdown]
# (t) sudo: 관리자(root) 권한으로 명령어 실행
# 
# ```bash
# sudo 명령어
# sudo rm 관리자_권한_폴더/관리자_권한_파일
# sudo nano 관리자_권한_폴더/관리자_권한_파일
# ```
# 
# 가장 많이 사용되는 `sudo` 명령어:<br>
# `sudo apt update`: 패키지 목록 갱신<br>
# `sudo apt upgrade`: 설치된 패키지를 최신 버전으로 업데이트<br>
# `sudo apt install <package_name>`: 패키지 설치

# %% [markdown]
# ---

# %% [markdown]
# ### B. 기초 Python 문법 복습

# %% [markdown]
# #### $1)$ 데이터 형식 (Data Types)

# %%
name = "철수"
age = 20
height = 178.31754081

print(f"이름: {type(name)}, 나이: {type(age)}, 키: {type(height)}")
print()
print("이름:", name, end=", ")
print("나이: " + str(age) + "세", end=", ")
print(f"키(m): {height/100:.2f}m", end=", ")
print(f"키(cm):", str(height)[0:5] + "cm", end="\n\n")
print("성인 여부:", age > 19)

# %%
score_str = "0.8825"

# TODO: score_str를 백분율로 표현한 뒤 정수로 변환하여 출력
score_int = int(float(score_str) * 100)
print(f"최종 점수: {score_int}점")  # 예상 출력 "최종 점수: 88점"

# %% [markdown]
# #### $2)$ List

# %% [markdown]
# ##### Constructing List

# %%
lst1 = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]  # length: 10, index: 0-9
lst2 = list((map(int, input("숫자 입력 (띄어쓰기로 구분): ").split())))

print(lst1)
print(lst2)

# %% [markdown]
# ##### List Indexing

# %%
print(lst1[0])
# print(lst1[3])
# print(lst1[9])
# print(lst1[10])

# print(lst1[-1])
# print(lst1[-2])
# print(lst1[-9])
# print(lst1[-10])

# %% [markdown]
# ##### Change Elements in a List

# %%
print(lst1)
lst1[0] = "X"
print(lst1)
lst1[-1] = "Y"
print(lst1)

# %%
print(lst1[0:1])

# print(lst1[0:9])

# print(lst1[0:10])

# print(lst1[0:len(lst1)])

# print(lst1[0:-1])

# print(lst1[:])

# print(lst1[:3])

# print(lst1[3:])

# print(lst1[0:5:2])

# print(lst1[::2])

# print(lst1[::-1])


# new_lst = lst1[:3] + lst2
# print(new_lst)

# %% [markdown]
# ##### Nested Loop

# %%
for i in range(10):        # i = 0           # i = 0           # i = 0
    for j in range(10):    # i = 0, j = 0    # i = 0, j = 1    # i = 0, j = 2
        my_tuple = (i,j)   # (0,0)           # (0,1)           # (0,2)
        print(my_tuple)
    print("\n------\n")

# %% [markdown]
# ##### List Comprehension

# %%
my_list1 = []

for i in range(10):
    my_list1.append(i)
    
print(my_list1)

# %%
test_lst1 = [0, 10, 100]
test_lst2 = [x * 100 for x in test_lst1]

print(test_lst2)

# %%
my_arr1 = []

for i in range(10):
    for j in range(10):
        my_arr1.append([i, j])


print(my_arr1)

# %%
my_arr2 = [[i, j] for i in range(10) for j in range(10)]

print(my_arr2)

# %%
my_arr3 = [[i, j] for i in range(10) for j in range(10) if i <= 1 and j <= 1]

print(my_arr3)

# %%
scores = [[45, 88, 92],
          [30, 75, 60],
          [95, 40, 50]]

# TODO: List Comprehension을 활용해 50점 이상인 값만 통과시켜 담으세요.
passed_scores = [score for row in scores for score in row if score >= 50]
print("합격 점수들:", passed_scores)

# %% [markdown]
# #### $3)$ Enumerate & Zip

# %%
students = ["짱구", "철수", "맹구"]
scores = [85, 92, 78]

# %%
print(tuple(enumerate(students)))

# %%
for idx, name in enumerate(students):
    print(f"{idx}번 학생: {name}")

# %%
for idx, name in enumerate(students, start=1):
    print(f"{idx}번 학생: {name}")

# %%
print(tuple(zip(students, scores)))

# %%
for name, score in zip(students, scores):
    print(f"{name}의 점수: {score}점")

# %%
# TODO: enumerate와 zip을 조합하거나 활용하여 80점 이상 학생 정보 출력
# TODO: 예상 출력 "1번 짱구 학생 (점수: 85점)"

for idx, (name, score) in enumerate(zip(students, scores), start=1):
    if score >= 80:
        print(f"{idx}번 {name} 학생 (점수: {score}점)")

# %% [markdown]
# #### $4)$ Dictionary, JSON, YAML

# %% [markdown]
# ##### Dictionary

# %%
student_info = {
    "name": "맹구",
    "age": 21,
    "major": "컴퓨터공학"
}

for key, value in student_info.items():
    print(f"{key}: {value}")

# %%
print("이름:", student_info["name"])

student_info["gpa"] = 3.8
student_info["languages"] = ["Python", "C++"]

print("학점:", student_info["gpa"])
print("프로그래밍 언어:", student_info["languages"])

# %% [markdown]
# ##### JSON

# %%
import json

# %%
with open("맹구_개인정보.json", "w", encoding="utf-8") as f:
    json.dump(student_info, f, ensure_ascii=False, indent=4)

print("JSON 파일 저장 완료!")

# %% [markdown]
# ##### YAML

# %%
import yaml

# %%
with open("맹구_개인정보.yaml", "w", encoding="utf-8") as f:
    yaml.safe_dump(student_info, f, allow_unicode=True, default_flow_style=False)

print("YAML 파일 저장 완료!")

# %% [markdown]
# #### $5)$ NumPy

# %% [markdown]
# ##### 기초 문법

# %%
import numpy as np

# %%
str_arr = np.array(["1.5", "2.8", "3.1"])
print(str_arr)

print()

float_arr = str_arr.astype(np.float64)
print(float_arr.dtype)
print(float_arr)

# %%
float_arr = np.array(["1.5", "2.8", "3.1"]).astype(np.float64)
print(float_arr.dtype)
print(float_arr)

# %% [markdown]
# ##### Indexing 및 Slicing

# %%
grid = np.array([
    [10, 20, 30],
    [40, 50, 60],
    [70, 80, 90]
])

print("우측 상단 2x2 영역:\n", grid[:2, 1:])

# %% [markdown]
# ##### NumPy 연산 함수

# %%
np_arr = np.array([1, 2, 3, 4, 5])

print("Sum:", np_arr.sum())
print("Minimum:", np_arr.min())
print("Maximum:", np_arr.max())
print("Mean:", np_arr.mean())
print("Argmax:", np_arr.argmax())

# %%
# [row: 학생, column: 과목]
scores = np.array([
    [83, 92, 100],  # 0번 학생
    [76, 63, 58],   # 1번 학생
    [91, 99, 84]    # 2번 학생
])

student_means = np.mean(scores, axis=1)
print("학생별 평균 (axis=1):")
print(student_means)

print()

subject_means = np.mean(scores, axis=0)
print("과목별 평균 (axis=0):")
print(subject_means)

# %%
scores = np.array([85, 90, 100, 60, 90, 75, 90])

is_ninety = (scores == 90)
print("90점인 점수 Boolean 배열:", is_ninety)
idx_ninety = np.where(scores == 90)
print(list(idx_ninety[0]))

print("90점인 학생 수:", (scores == 90).sum())
print("80점 이상인 학생 수:", (scores >= 80).sum())

# %%
# TODO: NumPy 연산 함수를 사용하여 배열을 정규화

np_arr_normalized = (np_arr - np_arr.min()) / (np_arr.max() - np_arr.min())
print(np_arr_normalized)

# %%
# TODO: NumPy 연산 함수를 사용하여 배열을 0.0 ~ 1.0 범위의 float32 배열로 변환
# TODO: 최종 배열의 최솟값 = 0.0, 최종 배열의 최댓값 = 1.0

np_arr_final = (
    (np_arr - np_arr.min()) / (np_arr.max() - np_arr.min())
).astype(np.float32)
print(np_arr_final)
print(np_arr_final.dtype)

# %% [markdown]
# ##### Flatten

# %%
grid = np.array([
    [10, 20, 30],
    [40, 50, 60],
    [70, 80, 90]
])

flat_grid = grid.flatten()

print("Flatten 이전 모양 (shape):", grid.shape)
print("Flatten 이후 모양 (shape):", flat_grid.shape)
print("Flatten된 배열:", flat_grid)

# %% [markdown]
# 이것으로 **『기초 Linux CLI 및 Python 문법』** 섹션을 마무리합니다.

# %% [markdown]
# ---

# %% [markdown]
# ## <center>< Section Project ></center>
# 
# 본 섹션에서 배운 내용을 토대로 프로젝트를 진행합니다.<br>
# 학교 전산 시스템에 5명 학생의 정보와 성적이 저장되어 있습니다. 학생 명단은 `"studentID_name"` 형식의 1차원 문자열 리스트로 저장되어 있으며, 성적은 5명 학생의 3개 과목 점수가 2차원 배열로 관리됩니다.<br>
# 제공된 데이터를 활용하여 아래 분석을 순서대로 수행해봅시다.

# %% [markdown]
# **데이터:**

# %%
raw_students = [
    "zg0505_짱구",
    "cs0822_철수",
    "mg0910_맹구",
    "yr0605_유리",
    "hi0205_훈이"
    ]

raw_scores = np.array([
    [95, 82, 88],   # 짱구
    [70, 65, 90],   # 철수
    [98, 100, 92],  # 맹구
    [60, 75, 80],   # 유리
    [81, 88, 84],   # 훈이
])

subjects = ["국어", "수학", "과학"]

# %% [markdown]
# **실행 코드:**

# %%
# TODO: list comprehension을 활용해 raw_students 리스트에서 이름만 추출한 리스트 생성
student_names = [raw_student_name[-2:] for raw_student_name in raw_students]
print("1. 학생 명단: ", end="")
for idx, name in enumerate(student_names):
    sep = ", " if idx < len(student_names) - 1 else "\n\n"
    print(name, end=sep)


# TODO: 전산 시스템에 입력된 모든 성적들을 1차원 배열로 출력 (총 15개)
all_scores = raw_scores.flatten()
print("2. 전산 시스템에 입력된 모든 성적: ", end="")
for idx, score in enumerate(all_scores):
    sep = ", " if idx < len(all_scores) - 1 else "\n\n"
    print(f"{score}점", end=sep)

# TODO: 전체 12개 성적 중 85점 이상 개수
over_85 = (raw_scores >= 85).sum()
print(f"3. 전체 15개 성적 중 85점 이상 개수: {over_85}개", end="\n\n")

# TODO: 학생별 평균 점수 출력
student_means = np.mean(raw_scores, axis=1)
result = ", ".join(
    f"{name} {score:.0f}점"
    for name, score in zip(student_names, student_means)
)

print(f"4. 학생별 평균 점수: {result}")
# 예상 출력: "4. 학생별 평균 점수: 짱구 00점, 철수 00점, 맹구 00점, 유리 00점, 훈이 00점"

# TODO: 평균 점수가 가장 높은 학생 선택
top_student_idx = np.argmax(student_means)
top_student_name = student_names[top_student_idx]
print(f"5. 최고 평균 수석 학생: {top_student_name} ({student_means[top_student_idx]:.2f}점)")

# TODO: 전체 점수 기준, 최고 점수와 과목 그리고 해당 학생 계산 (modulo % 사용 추천)
overall_max_score = np.max(raw_scores)
overall_max_idx = np.argmax(raw_scores)
overall_max_subject = subjects[overall_max_idx % raw_scores.shape[1]]
overall_max_student = student_names[overall_max_idx // raw_scores.shape[1]]
print(f"6. 단일 과목 최고 점수: {overall_max_score}점 (과목: {overall_max_subject}, 학생: {overall_max_student})")

# %% [markdown]
# ---
# ---

# %% [markdown]
# <br><br><div style="text-align: right; color: gray; font-style: italic;">
# © 2026 김규래 (Kyu Rae Kim). All rights reserved.&emsp;<br><br>
# This material is provided solely for the intended instructional purpose.&emsp;<br>
# Redistribution, reproduction, modification, adaptation, or reuse of this material in any form without prior written permission from the copyright holder is prohibited.&emsp;
# </div>


