# 팔로잉 자동 좋아요

Google Chrome에서 Instagram의 **시간순 팔로잉 피드**를 확인하고, 광고·추천·이미 좋아요한 게시물을 제외한 미좋아요 게시물에 좋아요를 누르는 데스크톱 앱입니다.

## 사용법

1. Releases 또는 성공한 `Following Auto Liker` Actions 실행에서 운영체제에 맞는 ZIP을 받습니다.
2. 압축을 풀고 앱을 실행합니다. Google Chrome이 설치되어 있어야 합니다.
3. **시작**을 누릅니다.
4. 첫 실행에서는 열린 전용 Chrome 창에서 Instagram에 직접 로그인합니다. 앱은 아이디·비밀번호·2단계 인증 코드를 받거나 저장하지 않습니다.
5. 앱과 Chrome 창을 열어 둡니다. 중지하거나 앱을 닫으면 Chrome 자동화도 정상 종료됩니다.

일반적으로 쓰는 Chrome 프로필이 아니라 앱 전용 프로필을 사용합니다. 이후 실행에서는 그 전용 프로필의 로그인 쿠키를 재사용합니다.

## 기본 설정

| 설정 | 기본값 |
| --- | ---: |
| 피드 확인 간격 | 30분 |
| 좋아요 사이 대기 | 무작위 3~5초 |
| 회차당 최대 좋아요 | 0개(제한 없음) |
| 최대 피드 화면 수 | 120회 |

3~5초는 빠른 설정이며 계정 제한 위험이 있습니다. Instagram이 활동 제한 문구를 표시하면 앱은 즉시 중지하며 제한이나 본인 확인을 우회하지 않습니다.

## 처리 범위

앱은 Instagram 웹이 `?variant=following` 피드에 실제로 불러온 게시물만 처리합니다. 다음 항목은 제외합니다.

- 이미 좋아요한 게시물
- 광고 또는 Sponsored 게시물
- 추천 게시물
- 좋아요 버튼 상태를 확실히 판별할 수 없는 게시물

Instagram이 URL이나 HTML 구조를 변경하면 자동화가 중단될 수 있습니다. 시간순 팔로잉 피드가 아닌 화면으로 이동하면 일반 홈 피드를 잘못 처리하지 않고 중지합니다.

## 저장 위치

앱의 **데이터 폴더 열기** 버튼으로 확인할 수 있습니다.

- `config.json`: 확인 간격과 좋아요 간격 등 설정
- `chrome-profile/`: 앱 전용 Chrome 로그인·쿠키·사이트 데이터
- `app.log`: 오류 기록

`chrome-profile/`은 계정 세션을 포함할 수 있으므로 공유하지 마세요. **전용 Chrome 로그인 지우기** 버튼은 이 폴더만 초기화하며 일반 Chrome 데이터에는 영향을 주지 않습니다.

## 개발 실행

Python 3.10 이상과 Google Chrome이 필요합니다.

```bash
python -m pip install -e .
python -m pip install -r apps/following_auto_liker/requirements.txt
python -m apps.following_auto_liker.app
```

테스트:

```bash
python -m unittest -v tests.regression.test_following_auto_liker
```

로컬 패키징:

```bash
python -m pip install pyinstaller
pyinstaller --noconfirm --clean --onefile --windowed \
  --name FollowingAutoLiker --paths . --collect-all playwright \
  apps/following_auto_liker/app.py
```
