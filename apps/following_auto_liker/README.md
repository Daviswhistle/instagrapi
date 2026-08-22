# 팔로잉 자동 좋아요

Google Chrome에서 Instagram의 **시간순 팔로잉 피드**를 열고, 발견한 미좋아요 게시물에 모두 좋아요를 누르는 데스크톱 앱입니다.

기존 버전처럼 Instagram 아이디와 비밀번호를 앱에 입력하지 않습니다. 앱이 별도의 Chrome 프로필을 열며, 첫 실행에 그 창에서 직접 로그인하면 이후 실행에도 로그인 상태가 남습니다.

## 동작 방식

1. 자동 좋아요 전용 Chrome 프로필로 Google Chrome을 엽니다.
2. `https://www.instagram.com/?variant=following`을 열어 팔로잉 계정의 시간순 피드를 표시합니다.
3. 위에서 아래로 스크롤하며 게시물과 릴스를 찾습니다.
4. 광고·추천 게시물과 이미 좋아요한 게시물은 건너뜁니다.
5. 나머지 게시물은 설정한 무작위 간격을 두고 전부 좋아요 처리합니다.
6. "모두 확인했습니다" 표시, 더 이상 움직이지 않는 피드, 또는 최대 스크롤 횟수에 도달하면 해당 확인을 마칩니다.
7. 설정한 시간이 지나면 피드 맨 위부터 다시 확인합니다.

좋아요 확률, 최초 실행 기준선, 일일 한도는 없습니다. **회차당 최대 좋아요가 `0`이면 해당 확인에서 발견한 미좋아요 글을 제한 없이 처리**합니다.

## 가장 쉬운 사용법

1. 저장소의 **Releases**에서 운영체제에 맞는 ZIP을 받습니다.
   - Windows: `FollowingAutoLiker-Windows.zip`
   - Apple Silicon Mac: `FollowingAutoLiker-macOS-Apple-Silicon.zip`
   - Intel Mac: `FollowingAutoLiker-macOS-Intel.zip`
2. ZIP을 풀고 앱을 실행합니다. Google Chrome이 설치되어 있어야 합니다.
3. 기본 설정을 그대로 두고 **시작**을 누릅니다.
4. 처음 열린 Chrome 창에서 Instagram에 직접 로그인합니다. 2단계 인증이나 본인 확인도 그 창에서 직접 완료합니다.
5. 앱과 앱이 연 Chrome 창을 열어 둡니다. 이후에는 저장된 로그인 상태를 사용합니다.

Windows SmartScreen이나 macOS Gatekeeper가 경고할 수 있습니다. 배포 파일에 코드 서명을 하지 않았기 때문입니다. macOS에서는 앱을 Control-클릭한 뒤 **열기**를 선택하면 됩니다.

## 기본 설정

| 설정 | 기본값 | 의미 |
| --- | ---: | --- |
| 피드 확인 간격 | 30분 | 한 번 끝까지 확인한 뒤 다음 확인까지 대기 |
| 좋아요 전 최소 대기 | 3초 | 각 좋아요 사이 무작위 대기의 하한 |
| 좋아요 전 최대 대기 | 5초 | 각 좋아요 사이 무작위 대기의 상한 |
| 회차당 최대 좋아요 | 0개 | `0`은 제한 없음 |
| 최대 스크롤 횟수 | 120회 | 피드 또는 UI 이상으로 무한 스크롤하는 상황 방지 |

좋아요를 전부 누른다는 목표와 Instagram의 활동 제한 위험은 동시에 완전히 만족시킬 수 없습니다. 기본 3~5초는 상당히 공격적인 설정입니다. 앱은 수량을 임의로 줄이지 않는 대신 Instagram이 제한 안내를 표시하면 더 진행하지 않고 즉시 중지하며, 필요하면 화면에서 간격을 늘릴 수 있습니다.

## 로그인과 저장 데이터

앱은 일반 Chrome의 기본 프로필을 직접 자동화하지 않습니다. 대신 다음 위치의 **전용 Chrome 프로필**을 사용합니다.

- Windows: `%LOCALAPPDATA%\FollowingAutoLiker\chrome-profile`
- macOS: `~/Library/Application Support/FollowingAutoLiker/chrome-profile`

이 폴더에는 Instagram 쿠키와 로그인 세션이 들어 있으므로 비밀번호에 준하는 민감한 데이터로 취급해야 합니다. 앱의 **전용 Chrome 데이터 지우기**를 누르면 이 로그인과 사이트 데이터가 삭제됩니다. 일반 Chrome의 기록과 로그인에는 영향을 주지 않습니다.

앱 자체 설정과 로그는 같은 데이터 폴더에 저장됩니다.

- `config.json`: 확인 간격과 대기 시간 등의 설정
- `chrome-profile/`: 전용 Chrome의 로그인·쿠키·사이트 데이터
- `app.log`: 오류와 동작 기록

## "전부"의 한계

이 앱은 Chrome에 실제로 표시되는 시간순 팔로잉 피드를 끝까지 훑습니다. 따라서 홈 알고리즘 피드만 읽던 기존 방식보다 누락 가능성이 낮습니다. 다만 다음 이유로 100%를 보증할 수는 없습니다.

- Instagram이 특정 게시물을 웹 팔로잉 피드에 제공하지 않거나 팔로잉 URL을 변경한 경우
- 게시물이 삭제되거나 접근 권한이 바뀐 경우
- Instagram이 HTML 구조나 버튼 이름을 변경한 경우
- 계정 활동 제한, 네트워크 오류, Chrome 종료가 발생한 경우
- 최대 스크롤 횟수 안에 피드 끝에 도달하지 못한 경우

앱은 팔로잉 계정의 프로필을 수백·수천 개씩 개별 방문하지 않습니다. 그렇게 하면 요청량과 계정 제한 위험이 훨씬 커지기 때문입니다.

## 중요한 위험

이 기능은 Meta가 제공하는 공식 자동 좋아요 API가 아니라 실제 Instagram 웹 화면을 조작합니다. Instagram의 정책이나 탐지 방식에 따라 로그인 확인, 일시적 활동 제한, 좋아요 제한 또는 계정 제한이 발생할 수 있습니다.

앱은 자동화 탐지를 숨기거나 Challenge를 우회하지 않습니다. 다음과 같은 제한 문구가 보이면 즉시 중지합니다.

- `Try again later`
- `We restrict certain activity`
- `Action blocked`
- `나중에 다시 시도하세요`
- `특정 활동을 제한합니다`

영어·한국어뿐 아니라 일본어·스페인어·프랑스어·독일어·포르투갈어·러시아어·중국어의 주요 제한 문구도 감지합니다. 제한 문구는 게시물 캡션이 아니라 Instagram의 대화상자·알림 UI에서만 판정합니다.

문제가 생기면 앱을 중지하고 공식 Instagram 앱 또는 웹에서 계정 상태를 확인해야 합니다.

## 개발 실행

Python 3.10 이상과 Google Chrome이 필요합니다.

```bash
python -m pip install -e .
python -m pip install -r apps/following_auto_liker/requirements.txt
python -m apps.following_auto_liker.app
```

설치된 Google Chrome을 `channel="chrome"`으로 실행하므로 `playwright install`로 별도 Chromium을 내려받을 필요는 없습니다.

테스트:

```bash
python -m unittest -v tests.regression.test_following_auto_liker
```

로컬 패키징:

```bash
python -m pip install pyinstaller
pyinstaller --noconfirm --clean --windowed --onedir \
  --name FollowingAutoLiker --paths . \
  --collect-all playwright --copy-metadata playwright \
  apps/following_auto_liker/app.py
```

## 배포 빌드

`.github/workflows/following-auto-liker.yml`은 Windows와 Apple Silicon/Intel macOS 패키지를 만듭니다.

- Pull request와 `master` 반영 시: 테스트 후 Actions 산출물 생성
- 수동 실행: Actions의 `Following Auto Liker`에서 `Run workflow`
- `following-auto-liker-v*` 태그 푸시 시: 세 운영체제 패키지를 GitHub Release로 게시
