# 팔로잉 자동 좋아요

내가 실제로 팔로우한 계정의 새 피드 게시물을 주기적으로 확인하고, 보수적인 속도로 좋아요를 누르는 데스크톱 앱입니다. 추천 게시물, 광고, 이미 좋아요한 게시물, 설정한 시간보다 오래된 게시물은 제외합니다.

## 가장 쉬운 사용법

1. 저장소의 **Releases**에서 운영체제에 맞는 압축 파일을 받습니다.
   - Windows: `FollowingAutoLiker-Windows.zip`
   - Apple Silicon Mac: `FollowingAutoLiker-macOS-Apple-Silicon.zip`
   - Intel Mac: `FollowingAutoLiker-macOS-Intel.zip`
2. 압축을 풀고 앱을 실행합니다.
3. 인스타그램 사용자 이름과 비밀번호를 입력하고 **시작**을 누릅니다.
4. 2단계 인증을 사용하는 계정은 안내가 나타났을 때 Instagram에서 받은 코드나 인증 앱의 현재 코드를 입력하고 다시 시작합니다.
5. 앱 창을 열어 둡니다. 컴퓨터가 잠자기 상태가 되거나 앱을 종료하면 확인도 중단됩니다.

아직 Release가 없다면 GitHub의 **Actions → Following Auto Liker → 최근 성공한 실행**에서 빌드 산출물을 받을 수 있습니다.

Windows SmartScreen이나 macOS Gatekeeper가 경고할 수 있습니다. 현재 빌드는 코드 서명을 하지 않기 때문입니다. macOS에서는 앱을 Control-클릭한 뒤 **열기**를 선택하면 됩니다.

## 첫 실행에서 일어나는 일

첫 실행은 현재 홈 피드에 보이는 게시물을 전부 **기준선**으로만 기록합니다. 기존 게시물에 좋아요를 몰아서 누르지 않으며, 그다음 확인부터 새로 발견되는 게시물만 처리합니다.

기본 설정은 다음과 같습니다.

- 하루 최대 30개
- 발견한 후보 중 90%에 좋아요
- 15분마다 홈 피드 확인
- 좋아요 전에 90~240초 무작위 대기
- 게시 후 24시간 이내의 글만 처리
- 팔로잉 목록은 하루에 한 번 갱신

모든 값은 앱에서 바꿀 수 있습니다. 일일 한도에 도달해 남은 게시물은 처리 기록에 넣지 않으므로, 다음 날에도 새 글 인정 시간 안에 있으면 다시 후보가 될 수 있습니다.

## 개인정보와 저장 위치

비밀번호와 2단계 인증 코드는 저장하지 않습니다. 로그인 성공 후에는 Instagram 세션과 기기 프로필만 로컬에 저장합니다. 세션 파일은 비밀번호에 준하는 민감한 정보이므로 다른 사람에게 전달하지 마세요.

앱의 **데이터 폴더 열기** 버튼으로 다음 파일을 확인할 수 있습니다.

- `config.json`: 앱 설정과 마지막 사용자 이름
- `accounts/<계정 식별자>/session.json`: 로그인 세션
- `accounts/<계정 식별자>/state.json`: 처리한 게시물, 일일 횟수, 팔로잉 캐시
- `app.log`: 앱 자체 오류 기록

**계정 데이터 초기화**는 선택한 계정의 세션과 처리 기록을 모두 지웁니다. 다른 계정으로 바꾸거나 기준선을 새로 만들 때 사용합니다.

## 중요한 한계와 위험

이 앱은 Meta의 공식 Instagram API가 아니라 `instagrapi`의 Private API를 사용합니다. Instagram의 정책이나 탐지 방식에 따라 로그인 확인, 일시적 요청 제한, 기능 제한이 발생할 수 있습니다. 이를 회피한다고 보장하지 않으며, 문제가 생기면 자동화를 즉시 중지하고 공식 앱에서 계정 상태를 확인해야 합니다.

또한 이 앱은 모든 팔로잉 계정을 개별 순회하지 않고 Instagram이 반환한 **홈 타임라인**을 확인합니다. API 호출 수를 과도하게 늘리지 않는 대신, 홈 피드에 노출되지 않은 게시물까지 100% 처리한다고 보장할 수는 없습니다.

## 개발 실행

Python 3.10 이상이 필요합니다.

```bash
python -m pip install -e .
python -m apps.following_auto_liker.app
```

테스트:

```bash
python -m unittest -v tests.regression.test_following_auto_liker
```

로컬 실행 파일 빌드:

```bash
python -m pip install pyinstaller
pyinstaller --noconfirm --clean --onefile --windowed \
  --name FollowingAutoLiker --paths . --collect-all instagrapi \
  apps/following_auto_liker/app.py
```

## 배포 빌드

`.github/workflows/following-auto-liker.yml`은 Windows와 Apple Silicon/Intel macOS 패키지를 만듭니다.

- Pull request와 `master` 반영 시: Actions 산출물 생성
- 수동 실행: Actions에서 `workflow_dispatch`
- `following-auto-liker-v*` 태그 푸시 시: 빌드 후 GitHub Release 자동 생성

예시:

```bash
git tag following-auto-liker-v0.1.0
git push origin following-auto-liker-v0.1.0
```
