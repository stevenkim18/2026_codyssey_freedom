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

## 동일 artifact 재시도로 복구

`deploy`에 원래의 신뢰된 bundle과 동일 SHA-256을 다시 전달하면 검증 가능한
중단 상태를 재개한다. 별도 repair 명령이나 state 수동 편집은 필요하지 않다.
실제 CLI의 기존 `deploy --role ... --spec ...` 경로를 사용하되 소스와 번들이
중단된 배포와 동일함을 확인한다. `recovery` 명령 자체는 계속 읽기 전용이다.

- state는 현재 링크와 일치하거나, 정확히 새 release를 current로 기록하고
  실제 링크를 previous로 기록한 전환 직전 상태여야 한다. 이전 release가 있는
  경우 기존 서비스 건강 상태와 release 무결성을 먼저 검사한다.
- manifest가 완성된 stage는 기존 무결성 검사 외에 요청 bundle의 실제 파일
  목록·바이트 해시와 대조한다. manifest만 일관되게 바꾼 코드도 거부한다.
- manifest 없는 stage는 bundle에 있는 디렉터리·파일만 허용하고 각 파일은
  원본의 정확한 접두 바이트여야 한다. symlink, hardlink, 알 수 없는 경로와
  변경된 바이트를 거부한다. 검증을 통과한 부분 stage 전체를 독점 생성한
  `preserved-stage-<uuid>/<sha256>`에 이동해 보존한 후 신뢰된 bundle을 새로 푼다.
  원본 내용·파일 mtime은 유지하며 삭제하지 않는다.
- 전환이 필요한 재개는 기존 service 재시작·readiness·실패 시 복구 경로를 따른다.
  요청 release가 이미 건강한 current이면 무결성 검사 후 재시작 없이 반환한다.
  성공 후 state의 previous를 이용한 정상 rollback도 가능하다. 이전 archive를
  가리키는 state의 previous SHA에 대한 deploy는 계속 거부한다.

알 수 없는 state, 누락·변조된 state(기존 current가 있는 경우), 잘못된 release,
명시적 실패 기록(`failed`), release 내부의 미등록 atomic JSON 임시 파일 등은
자동 추측하지 않는다. 이 경우 파일과 배포 증거를 보존하고 별도 운영 검토가
필요하다. root의 `state.json.pending-*`는 재시도에서 열거하거나 정리하지 않는다.
state 기록 후 current 전환 전의 직접 rollback은 계속 거부된다. 먼저 동일
artifact deploy를 완료하거나 검토된 복구 절차를 사용한다. 이 변경은 모든
가능한 중단 지점의 자동 복구를 보장하지 않는다.

`STOP_MUTATIONS`를 포함한 진단 조치 코드는 권고이며 명령을 자동 실행하지
않는다. 재시도는 SSH dispatch의 기존 사용자별 배포 락 아래서 수행한다.
같은 사용자 권한으로 동시에 파일을 변경하는 외부 프로세스를 막거나 artifact
출처를 서명으로 인증하는 기능은 아니다.

프로세스 종료 검증은 **전원 손실·파일시스템 캐시 손실·fsync 내구성 검증이
아니다**. 실제 systemd 재시작, C DB 내구성, GPU 및 서버 네트워크 검증도 포함하지
않는다. stage release는 최대 1,000개까지만 열거하며 초과 시 진단을 거부한다.

```sh
python -m pytest deployment/tests/test_recovery_report.py -q
```
