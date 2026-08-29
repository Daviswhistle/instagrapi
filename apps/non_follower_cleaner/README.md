# 미팔로워 정리

내가 팔로우하지만 나를 팔로우하지 않는 Instagram 계정을 찾고, 목록을 검토한 뒤 선택한 계정만 언팔로우하는 데스크톱 앱입니다.

기존 **팔로잉 자동 좋아요** 앱과 같은 전용 Chrome 프로필, 로그인 상태, 데이터 폴더와
단일 실행 잠금을 공유합니다. 두 앱을 동시에 실행할 수 없으므로 같은 Chrome 프로필을 함께
조작하는 상황을 막습니다.

## 동작 방식

1. 전용 Google Chrome을 열고 저장된 Instagram 로그인을 확인합니다. 첫 사용이면 열린 Chrome에서 직접 로그인합니다.
2. 로그인한 브라우저 안에서 Instagram의 팔로잉 목록과 팔로워 목록을 끝까지 페이지 단위로 가져옵니다.
3. `팔로잉 - 팔로워` 차집합을 계산해 미팔로워 목록을 표시합니다.
4. 사용자가 목록을 검토하고 언팔로우할 계정을 선택합니다.
5. 실행 직전에 팔로워·팔로잉 전체 목록을 다시 확인합니다. 그 사이 관계가 바뀐 계정은 자동으로 제외합니다.
6. 각 계정의 현재 `followed_by`와 `following` 상태를 언팔로우 직전에 다시 확인합니다.
7. 여전히 내가 팔로우하고 상대는 나를 팔로우하지 않는 계정만 설정한 무작위 간격으로 언팔로우합니다.

목록 페이지 커서가 반복되거나, Instagram이 팔로워 목록 일부만 제공하거나, 응답 형식이
불완전하면 **한 명도 언팔로우하지 않고 중지**합니다. 각 언팔로우도 Instagram이
`following=false`를 명시적으로 반환해야 완료로 기록합니다.

## 기본 설정

| 설정 | 기본값 | 의미 |
| --- | ---: | --- |
| 언팔로우 전 최소 대기 | 10초 | 각 언팔로우 전 무작위 대기의 하한 |
| 언팔로우 전 최대 대기 | 18초 | 각 언팔로우 전 무작위 대기의 상한 |
| 회차당 최대 언팔로우 | 40개 | `0`은 선택한 계정 전부 |
| 목록 페이지 대기 | 1초 | 팔로워·팔로잉 페이지 요청 사이 대기 |

기본값은 10~18초와 회차당 40개로 제한하지만, 이 수치가 계정 안전을 보장하지는 않습니다.
계정 상태와 최근 활동량에 따라 회차 한도를 더 낮추거나 간격을 늘려야 할 수 있습니다. 앱은
탐지를 우회하지 않으며, 활동 제한·본인 확인·429·403 응답을 받으면 추가 작업을 중지합니다.

## 가장 쉬운 사용법

저장소의 Releases 또는 GitHub Actions 산출물에서 운영체제에 맞는 ZIP을 받습니다.

- Windows: `NonFollowerCleaner-Windows.zip`
- Apple Silicon Mac: `NonFollowerCleaner-macOS-Apple-Silicon.zip`
- Intel Mac: `NonFollowerCleaner-macOS-Intel.zip`

ZIP을 풀고 앱을 실행한 뒤 **목록 확인**을 누릅니다. 첫 실행에 열린 Chrome에서 직접 로그인하면 이후에는 기존 로그인 상태를 재사용합니다.

## 저장 데이터

기존 자동 좋아요 앱과 같은 데이터 폴더를 사용합니다.

- Windows: `%LOCALAPPDATA%\FollowingAutoLiker`
- macOS: `~/Library/Application Support/FollowingAutoLiker`

주요 파일:

- `chrome-profile/`: Instagram 쿠키와 로그인 세션
- `non_follower_cleaner.json`: 언팔로우 간격과 회차 한도
- `app.log`: 자동 좋아요와 미팔로워 정리의 실행 기록

`chrome-profile/`은 비밀번호에 준하는 민감한 데이터로 취급해야 합니다.

## 개발 실행

Python 3.10 이상과 Google Chrome이 필요합니다.

```bash
python -m pip install -e .
python -m pip install -r apps/following_auto_liker/requirements.txt
python -m apps.non_follower_cleaner.app
```

테스트:

```bash
python -m unittest -v tests.regression.test_non_follower_cleaner
```

로컬 패키징:

```bash
python -m pip install pyinstaller
pyinstaller --noconfirm --clean --windowed --onedir \
  --name NonFollowerCleaner --paths . \
  --collect-all playwright --copy-metadata playwright \
  apps/non_follower_cleaner/app.py
```

## 배포 빌드

`.github/workflows/non-follower-cleaner.yml`은 Windows와 Apple Silicon/Intel macOS 패키지를 만듭니다.

- Pull request와 `master` 반영 시: 테스트 후 Actions 산출물 생성
- 수동 실행: Actions의 `Non-Follower Cleaner`에서 `Run workflow`
- `non-follower-cleaner-v*` 태그 푸시 시: 세 운영체제 패키지를 GitHub Release로 게시

## 중요한 위험

이 앱은 Meta가 제공하는 공식 계정 관리 API가 아니라 Instagram 웹 앱의 내부 엔드포인트를
로그인된 Chrome 세션에서 호출합니다. Instagram의 약관이나 탐지 방식에 따라 일시적 활동
제한, 본인 확인, 로그인 만료 또는 계정 제한이 발생할 수 있습니다. Instagram이 엔드포인트나
응답 형식을 변경하면 앱은 안전하게 중지하도록 설계했지만, 장기적인 호환성을 보장할 수는
없습니다.
