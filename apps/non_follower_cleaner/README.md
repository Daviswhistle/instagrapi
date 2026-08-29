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
5. 앱은 목록 확인 결과에서 사용자가 선택한 계정을 작업 대상으로 고정합니다.
6. 실행 전 전체 팔로워·팔로잉 목록이나 개별 관계를 다시 조회하지 않습니다. 목록을 확인한 계정과 현재 로그인 계정이 같은지만 확인합니다.
7. 선택한 계정을 설정한 무작위 간격으로 차례로 언팔로우합니다.

Instagram이 필터링된 항목 때문에 빈 중간 페이지와 다음 커서를 함께 보낼 수 있습니다.
이 경우 커서가 정상적으로 바뀌는 동안은 다음 페이지를 계속 확인합니다. 반대로 목록 페이지
커서가 반복되거나, Instagram이 팔로워 목록 일부만 제공하거나, 응답 형식이 불완전하면
목록 확인을 실패로 처리합니다. 사용자가 목록을 확인하고 대상을 선택한 뒤에는 그 선택만
직접 실행하며 관계 목록을 다시 조회하지 않습니다. 각 언팔로우도 Instagram이
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

저장소의 Releases 또는 GitHub Actions 산출물에서 운영체제에 맞는 패키지를 받습니다.

- Windows: `NonFollowerCleaner-Windows.zip`
- Apple Silicon Mac: `NonFollowerCleaner-macOS-Apple-Silicon.dmg`
- Intel Mac: `NonFollowerCleaner-macOS-Intel.dmg`

GitHub Actions의 산출물은 GitHub가 한 번 더 ZIP으로 감싸서 제공합니다. Actions에서 받은 ZIP은
한 번만 풀면 Windows에서는 앱 ZIP, macOS에서는 DMG가 나옵니다. Mac에서는 DMG를 열고
`NonFollowerCleaner.app`을 `Applications`로 옮긴 뒤 실행합니다. Releases에서는 DMG를 직접 받을 수 있습니다.

현재 macOS 앱은 Developer ID 서명과 Apple 공증을 하지 않은 빌드입니다. 첫 실행이 차단되면
다음 순서로 본인이 받은 파일임을 확인한 뒤 예외를 허용합니다.

1. `Applications`의 `NonFollowerCleaner.app`을 한 번 실행해 차단 경고를 표시합니다.
2. **Apple 메뉴 → 시스템 설정 → 개인정보 보호 및 보안**으로 이동합니다.
3. 아래의 **보안** 영역에서 `NonFollowerCleaner`에 대한 **확인 없이 열기(Open Anyway)**를 누릅니다. 이 버튼은 앱 실행을 시도한 뒤 제한된 시간 동안만 표시됩니다.
4. 로그인 암호 또는 Touch ID로 승인하고, 다시 나타나는 경고에서 **열기**를 누릅니다.

출처를 신뢰하고 파일이 변조되지 않았다고 확신할 때만 이 예외를 허용해야 합니다. 앱을 실행한 뒤
**목록 확인**을 누르고, 첫 사용이면 열린 Chrome에서 직접 로그인합니다. 이후에는 기존 로그인 상태를 재사용합니다.

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

`.github/workflows/non-follower-cleaner.yml`은 Windows ZIP과 Apple Silicon/Intel macOS DMG를 만듭니다.
macOS 빌드는 `hdiutil verify`까지 통과해야 산출물로 업로드됩니다.

- Pull request와 `master` 반영 시: 테스트 후 Actions 산출물 생성
- 수동 실행: Actions의 `Non-Follower Cleaner`에서 `Run workflow`
- `non-follower-cleaner-v*` 태그 푸시 시: 세 운영체제 패키지를 GitHub Release로 게시

## 중요한 위험

이 앱은 Meta가 제공하는 공식 계정 관리 API가 아니라 Instagram 웹 앱의 내부 엔드포인트를
로그인된 Chrome 세션에서 호출합니다. Instagram의 약관이나 탐지 방식에 따라 일시적 활동
제한, 본인 확인, 로그인 만료 또는 계정 제한이 발생할 수 있습니다. Instagram이 엔드포인트나
응답 형식을 변경하면 앱은 안전하게 중지하도록 설계했지만, 장기적인 호환성을 보장할 수는
없습니다.
