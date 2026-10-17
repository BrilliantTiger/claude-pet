# v1.1.1 — 실행 게이트, 조정자 서명, 릴리즈 운영자 지정

이 릴리즈의 조정자 `release-coordinator-v111`(2026-10-17)이 쓴다. 이 릴리즈에서 다른 역할은 맡지 않았다.
이 파일 하나가 AGENTS.md §6 항목 1–5 를 v1.1.1 에 대해 채운다.

## 무엇을 릴리즈하나
- **끝 커밋:** `claude/release-v1.1.1-gate` 의 이 기록 커밋. 그 바로 아래가 `778a34a`(핀, `claude/keychain-quiet` 의 끝).
  - 바탕: dev `99f70e9`(앱의 레포 주소를 BrilliantTiger/claude-pet 으로).
  - 릴리즈 커밋: `d65bd9c` "release: ClaudePet v1.1.1" (APP_VERSION 1.1.1, RELEASE_NOTES v1.1.1).
  - 기능: 키체인 창 반복 수정 — `603e5b1`(30초 복호화 제거, 잠금 중 읽지 않기, 자동 경로의 네이티브 읽기 제거),
    `c725db9`(검토자 지적 R1–R3: 창 안에서도 잠금 중 네이티브 금지, 잠금 중 복구 스폰 보류, 잠김을 로그인 필요로
    읽지 않기, `verify_release_artifact.py` 사용 예의 버전 줄).
  - 테스트·핀: `28b9880`, `125de72`, `b1c8a3b`, `778a34a`.
- **사용자 승인, 원문 그대로.** 사용자가 메인 세션(keychain-quiet-dev)에 직접 쳤고, 릴리즈 커밋 `d65bd9c` 의 메시지에
  원문이 기록돼 있다. 조정자는 이를 전달받았다.
  - "빨리 끝내고 릴리즈까지 해"
  - 같은 세션 앞서, 수정을 시킨 말: "너뿐이 없으니까 내가 수정을 쳐하라고 시킨거 아니냐!"
  - 이는 릴리즈 순서 전체를 승인한다(AGENTS §6 일괄 승인). 게이트를 바꾸지 않고, 자격 없는 에이전트를 `[ASK-OP]`
    단계에 자격 있게 만들지도 않는다.

## 항목 1 — §8 조건 (릴리즈 전체)
- **트레일러:** 일곱 커밋 모두 `Developer:` / `Verifier:` 가 있고 서로 다르다. 프로덕션 커밋(`603e5b1`, `d65bd9c`,
  `c725db9`)은 Developer keychain-quiet-dev / Verifier keychain-quiet-tests, 테스트 커밋(`28b9880`, `125de72`,
  `b1c8a3b`, `778a34a`)은 그 반대.
- **분리(Condition B), `git log --stat 99f70e9..778a34a` 로 확인:** 테스트 커밋 넷은 `tests/` 만 건드린다
  (`test_keychain_quiet.py`, `test_oauth_token_cache.py`, `test_v1_1_1_release_contract.py`(이름 옮김),
  `test_manual_update_transaction.py`, `test_upload_artifact_gate.py`). Developer 커밋 셋은 `claude_pet.py`,
  `verify_release_artifact.py`, `RELEASE_NOTES.md` 만 건드린다. 두 집합은 겹치지 않는다.
- **먼저 빨강:**
  - `28b9880`: CI run 39030168251 — `Ran 1057 tests`, `FAILED (failures=21, errors=5, skipped=15)`; 실패는
    `test_keychain_quiet`·`test_oauth_token_cache` 안에만 있다(로그에서 확인). 커밋 본문의 99f70e9 관측 21 실패 + 5 오류와 같다.
  - `125de72`(R1–R3): 커밋 본문에 `d65bd9c` 에서 관측한 4 실패가 실패 메시지와 함께 기록돼 있다.
  - `b1c8a3b`(계약): 커밋 본문에 `d65bd9c` 에서의 빨강 둘(사용 예 버전, 노트 1번 문장이 아직 사실이 아님)이 기록돼 있다.
  - 중간 CI(`d65bd9c` run 39031320610, `c725db9` run 39032398907)의 실패는 소스 해시 핀 하네스
    (`test_manual_update_transaction`, `test_upload_artifact_gate`)가 바뀐 소스를 거부한 것이며, `778a34a` 에서 다시 핀됐다.
- **검토자:** 별도 검토 에이전트. `d65bd9c` 에 수정 필요(지적 4건) → `c725db9` 에 승인. 막지 않는 지적 하나:
  잠금 중 보류된 스폰도 복구 시도 횟수에 들어간다 — 이후 과제로 둔다.
- **추적되지 않은 파일:** 이 기록 외에 아무것도 더하거나 지우지 않았다. 메인 체크아웃은 건드리지 않았다.

## 항목 2 — 깨끗한 트리에서 전체 스위트
- 조정자 worktree, HEAD `778a34a`, `git status --porcelain` 출력 없음. 저장소 루트에서:
  - `python3 -m unittest discover -s tests` → `Ran 1067 tests in 370.645s` / `OK (skipped=8)`, exit 0
  - `python3 -m unittest discover -s windows/tests -t .` → `Ran 271 tests in 1.679s` / `OK (skipped=23)`, exit 0
- 검증자 keychain-quiet-tests 의 같은 트리 결과(전달받음): 1067 OK (skipped=8), 271 OK (skipped=23) — 위와 같다.
- CI run 39032886821 (headSha `778a34aa7bc0…`): `macOS — full suite: success`, `Windows — gating contract: success`.
- 이 기록 커밋은 `docs-design/` 의 문서 하나만 더하므로 스위트 결과에 영향이 없다.

## 항목 3 — 릴리즈 노트
v1.1.1 섹션(`d65bd9c` 이후 바뀌지 않음): 최상위 불릿 3개, 중첩 0, 333자(공백 정규화), 해시·소스 경로·테스트 언급 없음,
크기·빈도의 모호한 표현 없음. 숫자는 버전 1.1.1 뿐. 레포 이름(BrilliantTiger/claude-pet)은 사용자가 볼 주소라 넣는다.
문장마다 `tests/test_v1_1_1_release_contract.py` 가 소스에 묶는다. v1.1.0 은 published(태그·GitHub 릴리즈 존재)로
핀돼 있고 한 글자도 바뀌지 않았다.

## 항목 4 — 조정자 서명
- **Windows 11 실기기(사용자 PC), `c725db9` 에서:** Windows 테스트 `Ran 271` OK, `build_win.py` exit 0, 산출물 게이트 통과,
  무서명(원래대로). `git diff c725db9 778a34a --stat` 은 테스트 두 파일뿐이라 출하 바이트(`claude_pet.py`, `windows/`,
  `verify_release_artifact.py`)는 같다.
- **인증:** v1.1.1 을 실행 단계에 들어가도 된다고 인증한다. **게이트: GREEN.**
- **확인하지 못한 것:**
  - Developer ID 서명·공증된 v1.1.1 번들의 실제 Mac 실행(운영자가 빌드한 뒤에야 존재한다).
  - 실기기에서 로그인 키체인이 실제로 잠긴 상황. 이 동작은 가짜 객체를 쓴 테스트로만 확인됐다.

## 항목 5 — 릴리즈 운영자
- **자격 없음:** 조정자 release-coordinator-v111(이 기록의 작성자); keychain-quiet-dev(메인 세션, 개발자);
  keychain-quiet-tests(검증자); 이 릴리즈의 검토 에이전트; Windows 빌드 세션; 이전 릴리즈·사이트 작업의 모든 에이전트
  (release-operator-v110 포함).
- **지정 운영자:** `release-operator-v111`. 이 파일이 커밋된 뒤 이 일 하나만을 위해 새로 만든다. 이 릴리즈에서
  개발자·검증자·검토자·조정자 어느 역할도 맡지 않았다.
  - 하나씩, 단계마다 결과를 기록하고 첫 실패에서 멈춘다: `./release.sh build`, `sign`, `notarize`, `universal`, `dmg`,
    그다음 태그 `v1.1.1` 을 만들어 push, 그다음 `./release.sh publish`.
  - `all` / `ship` / 맨 `./release.sh` 는 절대 실행하지 않는다.
- **Windows 산출물:** 사용자 Windows PC 에서 `c725db9` 로 만들었다(무서명). 조정자 스크래치패드 `win111/` 에 있고,
  조정자가 다시 해시했다:
  - `claude-pet-win.zip` 72,632,523 바이트, `d1d8e1c3bfce34f31b25f9b63bd435963f93678e380e25a19d0517c4e6346486`
  - `claude-pet-win-setup.exe` 56,153,616 바이트, `92c0b6ae828b9ef8b52c19d54c859f2df3ba4a7b8a1ab4589e6420123a6d29e7`
  운영자는 해시를 다시 확인한 뒤 같은 릴리즈에 올린다(`--clobber` 없음).
