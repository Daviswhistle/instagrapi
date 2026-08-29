# Instagram 도구

팔로잉 자동 좋아요와 미팔로워 정리를 하나의 데스크톱 앱에서 사용하는 통합 버전입니다.

## 통합한 이유

두 기능은 원래 같은 전용 Chrome 프로필과 Instagram 로그인을 사용하면서도 별도 앱으로 실행됐습니다.
그 결과 기능을 바꿀 때마다 Chrome을 닫고 다시 열어야 했고, 로그인 확인 과정에서 홈 화면을 연속으로
탐색해 창이 두 번 열리거나 새로고침되는 것처럼 보일 수 있었습니다.

통합 앱은 Playwright를 소유하는 작업 스레드 하나와 Chrome 창 하나를 앱 종료까지 유지합니다.
자동 좋아요와 미팔로워 정리는 동시에 실행되지 않지만, 한 작업이 끝난 뒤 다른 탭의 작업을 시작해도
기존 Chrome 창과 로그인 세션을 그대로 사용합니다.

## 기능

### 자동 좋아요

- Instagram의 팔로잉 시간순 피드 확인
- 아직 좋아요하지 않은 일반 게시물만 처리
- 광고와 추천 게시물 제외
- 확인 간격, 좋아요 대기 시간, 회차 한도 설정

### 미팔로워 정리

- 내가 팔로우하는 계정과 나를 팔로우하는 계정 비교
- 나를 팔로우하지 않는 계정을 목록으로 표시
- 사용자가 선택한 계정만 언팔로우
- 실행 전 전체 목록이나 개별 관계를 다시 조회하지 않음
- 각 쓰기 요청 뒤에는 해당 계정 하나의 현재 팔로우 상태만 확인
- 실제 `following=false`가 확인된 경우에만 완료로 기록
- 기본 웹 엔드포인트가 관계를 바꾸지 않으면 Instagram 웹의 대체 언팔로우 엔드포인트를 한 번 시도

## 목록 확인 속도

기존 별도 앱은 팔로워와 팔로잉을 페이지당 100개씩 가져오고 페이지 사이에 1초를 기다렸습니다.
통합 앱은 페이지당 200개를 요청하고 페이지 사이 대기를 0.15초로 줄였습니다. 예를 들어 팔로워와
팔로잉이 각각 약 4,000개라면 페이지 사이에서 의도적으로 기다리는 시간은 약 78초에서 약 6초로
줄어듭니다. 실제 총 소요 시간에는 Instagram 서버 응답 시간도 포함됩니다.

전체 목록 비교는 실제 팔로워를 미팔로워로 잘못 분류하지 않기 위해 유지합니다. 따라서 계정 규모가
클수록 네트워크 요청 자체에 필요한 시간은 남습니다.

## 다운로드

GitHub Actions 실행 화면에는 운영체제별 산출물이 각각 표시됩니다. 필요한 파일 하나만 받습니다.

- Windows: `InstagramTools-Windows`
- Apple Silicon Mac: `InstagramTools-macOS-Apple-Silicon`
- Intel Mac: `InstagramTools-macOS-Intel`

Windows Actions 산출물은 실행 폴더 자체를 GitHub가 한 번만 ZIP으로 감싼 것입니다. 한 번 압축을
풀면 `InstagramTools.exe`와 실행에 필요한 파일이 나옵니다. 내부에 같은 이름의 ZIP을 다시 넣지 않습니다.

macOS Actions 산출물은 GitHub ZIP을 한 번 풀면 DMG가 나옵니다. DMG를 열고 `InstagramTools.app`을
`Applications`로 옮깁니다. GitHub Release에서는 Windows ZIP과 두 macOS DMG를 직접 받을 수 있습니다.

현재 macOS 빌드는 Developer ID 서명과 Apple 공증을 하지 않았습니다. 최초 실행이 차단되면 앱을
한 번 실행한 뒤 **시스템 설정 → 개인정보 보호 및 보안 → 보안 → 확인 없이 열기**에서 예외를 승인합니다.
출처와 파일 무결성을 신뢰할 때만 실행해야 합니다.

## 저장 데이터

기존 두 앱의 데이터와 로그인 상태를 그대로 사용합니다.

- Windows: `%LOCALAPPDATA%\FollowingAutoLiker`
- macOS: `~/Library/Application Support/FollowingAutoLiker`

주요 파일:

- `chrome-profile/`: Instagram 로그인 세션
- `config.json`: 자동 좋아요 설정
- `non_follower_cleaner.json`: 언팔로우 및 목록 확인 설정
- `app.log`: 두 기능의 공통 실행 기록

## 개발 실행

```bash
python -m pip install -e .
python -m pip install -r apps/following_auto_liker/requirements.txt
python -m apps.instagram_tools.app
```

테스트:

```bash
python -m unittest -v \
  tests.regression.test_instagram_tools \
  tests.regression.test_following_auto_liker \
  tests.regression.test_non_follower_cleaner \
  tests.regression.test_non_follower_cleaner_http_success
```

## 위험

이 앱은 Meta의 공식 계정 관리 API가 아니라 로그인된 Instagram 웹 세션의 내부 인터페이스를
사용합니다. Instagram의 약관이나 탐지 방식에 따라 활동 제한, 본인 확인, 로그인 만료 또는 계정
제한이 발생할 수 있습니다. 앱은 명시적 제한 신호를 받으면 후속 작업을 중지하지만 장기 호환성과
계정 안전을 보장하지 않습니다.
