# S04 읽기 전용 배포 상태 진단

이 도구는 관리된 배포 디렉터리의 파일 상태를 읽고 고정된 분류·운영 조치
코드를 출력한다. 링크 전환, state 수정, 서비스 재시작, 파일 정리·삭제를 하지
않는다. 현재 CLI는 **로컬 파일시스템용**이며 SSH 원격 실행과 연결하지 않았다.

```sh
# dev 디렉터리, Anaconda py312
python -m deployment.recovery --root /absolute/managed/root --role gpu
python -m deployment.recovery --root /absolute/managed/root --role general
```

정상 파일 상태는 종료 코드 0, 검토가 필요한 상태는 2다. 보고서에는 원문 경로,
manifest/state 내용, 비밀 또는 예외 원문을 넣지 않는다. `service_verified`와
`mutations_performed`는 항상 false다. `consistent`도 서비스 가동·readiness·TLS
또는 GPU 동작을 뜻하지 않는다. 출력 파일이 필요하면 운영자가 별도로 보관한다.

| 분류 | 읽은 상태 | 고정된 다음 조치의 의미 |
|---|---|---|
| `consistent` | 현재 링크·state·release 무결성이 일치 | 서비스 검증 후 사용 |
| `no_installation` | 경로가 없음 | 새 배포 계획 검토 |
| `incomplete_staged` | manifest 없는 release 등 불완전 staging | stage 보존, 중단된 배포 검토 |
| `staged_unreferenced` | 무결성은 맞지만 state가 참조하지 않는 release | 보존 및 기존 서비스·배포 기록 검토 |
| `state_ahead_of_current` | state가 새 release를 가리키고 링크는 이전 또는 없음 | 양쪽 보존, 현재 서비스 확인, state/링크 불일치 검토 |
| `state_current_mismatch` | 위 순서로 해석할 수 없는 state/링크 불일치 | 양쪽 보존 및 운영 기록 검토 |
| `previous_missing` | state의 이전 release가 없음 | 현재 보존, 신뢰된 원본 확보 또는 재계획 |
| `no_active_release` | 실패 release 기록은 있으나 현재 링크 없음 | 실패 release 보존 및 실패 원인 검토 |
| `untrusted_*`, `current_unknown`, `*_missing` | 경로·메타데이터·무결성 검사 실패 | 변경 중단 후 신뢰된 배포 기록 검토 |

`staged_unreferenced`는 정상적으로 남긴 오래된 archive와 완전히 써진 뒤 중단된
stage를 구분하지 못한다. 현재 state 형식에 전체 배포 이력이 없으므로 원인을
추측하지 않는다. manifest와 코드 둘 다 바꿀 수 있는 공격자에 대한 서명 검증도
아니다. 기존 `checked_release`의 파일 목록·해시·역할·디렉터리 이름 일치 계약을
사용하는 로컬 기준선 진단이다.

진단 전에 배포 writer를 멈춰 일관된 snapshot을 확보한다. 이 도구는 락 파일을
만들지 않으므로 동시 외부 변경을 막지 않는다. 복사된 mirror를 조사할 때도
state와 current에 기록된 절대 경로를 동일하게 유지한 파일시스템 view가 필요하다.
임의로 경로를 바꾸어 복사하면 미확인 경로로 거부하며 자동 경로 재해석은 없다.
읽기에 따른 운영체제 접근 시간 갱신 외에 파일 내용·mtime·링크를 수정하지 않는다.

## 확인된 중단 경계와 현재 한계

테스트는 실제 `remote.execute`를 별도 Python 프로세스에서 실행하고, 합성
prerequisite/service control/health 함수만 주입한다. 실제 파일 생성·manifest 기록·
state 교체·current 전환 뒤 지정된 지점에서 `os._exit(91)`로 프로세스를 끝낸다.
finally·정상 정리 코드가 실행되지 않은 파일 상태를 읽고 기존 release와 외부
sentinel이 유지되는지 확인한다.

확인된 기존 배포 도구의 한계:

- state 기록 후 current 전환 전 중단되면 같은 해시 deploy는
  `release_already_exists`, rollback은 `rollback_state_mismatch`로 거부될 수 있다.
- 첫 배포에서 manifest 전에 중단된 release 역시 같은 해시로 덮어쓰지 않는다.

이 진단기는 위 상태를 자동 복구하지 않는다. 파일과 배포 증거를 보존하고 실제
운영 기록·현재 서비스·신뢰된 artifact를 확인한 후 별도 승인된 복구 절차를
정해야 한다. `STOP_MUTATIONS`를 포함한 조치 코드는 진단 권고이며 어떠한 명령도
자동 실행하지 않는다.

프로세스 종료 검증은 **전원 손실·파일시스템 캐시 손실·fsync 내구성 검증이
아니다**. 실제 systemd 재시작, C DB 내구성, GPU 및 서버 네트워크 검증도 포함하지
않는다. stage release는 최대 1,000개까지만 열거하며 초과 시 진단을 거부한다.

```sh
python -m pytest deployment/tests/test_recovery_report.py -q
```
