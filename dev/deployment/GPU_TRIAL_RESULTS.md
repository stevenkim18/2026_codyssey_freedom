# M0 GPU API 시험 배포 결과

확인: 2026-10-31 UTC. B의 현재 M0 API 시험 결과다. 학습한 M1의 품질 인수나
외부 OpenAI·C 대시보드 통합 완료를 의미하지 않는다. 서버 주소·토큰·키·접속
비밀번호는 이 문서와 Git에 포함하지 않는다.

## 최종 상태

- Ubuntu 24.04 / NVIDIA L4 24GB에서 `codyssey-pii-gpu.service`가 실행 중이다.
- 원래 모델 `M0-1fcf13e85f4e-engineering`으로 복귀했다. 임계값 버전은
  `engineering-0.5-not-evaluated-v1`이다. 매핑·임계값의 A/B 품질 승인은 별도다.
- API 소스는 커밋 `5784a2aff50ea89816f79bc806bc9bcc29961eaf`에서 동결했다.
  application SHA-256은
  `fe8033399936f47998f7d7fb1546a5d9bb3aaf4e232e6da983f3ef642965ac83`이다.
- 실제 CUDA 로딩·예열을 확인했고, 서비스 MainPID와 NVIDIA compute PID가
  일치했다. 마지막 관찰의 GPU 메모리는 1,414MiB였다. 이는 용량·성능 SLO가 아니다.
- 해당 사용자의 linger와 B unit 자동 시작을 설정했다. 모든 작업용 SSH/SCP
  종료 및 대기 후 Mac의 HTTPS 호출이 다시 통과했다. VPN 종료로 서버 프로세스가
  멈추지 않으며, 이후 PC에서 사설 API를 호출하려면 VPN을 다시 연결해야 한다.

## 확인한 경로

| 검증 | 실제 결과 |
|---|---|
| 모델 전송·staging | 약 1.16GB archive와 파일/manifest SHA-256 검증, 별도 B 전용 경로 사용 |
| CUDA cold start | 기본 GLiNER loader와 실제 패키지의 로딩·예열 성공 |
| HTTPS·상태 | Mac에서 신뢰 CA와 hostname 검증, health 200·ready 200 |
| 인증 | 잘못된 토큰의 inspect 요청 401 |
| 검사 응답 | 합성 Unicode 요청 200/complete, 요청 ID·원문 코드포인트 범위·모델/임계값 버전 검증 |
| 무변경 게시 | 같은 패키지 `already_active`, 불필요한 재시작 없음 |
| 자동 교체 | 같은 M0 가중치의 명시적인 `-activation-test` 버전 게시 → 자동 재시작 → Mac에서 해당 버전 응답 확인 |
| 원복 | 원래 M0 게시 → 자동 재시작 → 모든 작업용 SSH 종료 후 Mac HTTPS 검사 재통과 |
| 실행 주체 | systemd 환경 파일·MainPID 환경·해당 PID의 실제 IPv4 listener 귀속 확인 |

시험 버전은 배포 동작을 검증하기 위한 동일 가중치의 메타데이터 버전이다.
학습 성과나 실제 M1 교체 증거로 사용하지 않는다. 원본과 시험 패키지·설정 이력은
보존했으며 삭제·디스크 초기화·A 데이터 변경을 수행하지 않았다.

## 발견한 문제와 반영

1. SSH 계정 이름으로 홈을 추정하면 실제 홈과 달랐다. 읽기 전용 탐색으로
   실제 홈·py312를 검증해 경로를 구성하도록 했다.
2. B py312에 Python protobuf가 없어 fast tokenizer 초기화가 실패했다.
   Conda 검색·dry-run 후 `protobuf 6.31.1 py312hb8af0ac_2`만 추가했고 기존
   패키지는 유지했다. 별도 새 프로세스에서 경로·메타데이터·protobuf 직렬화와
   실제 bundled tokenizer를 확인한 뒤 CUDA/API cold start를 검증했다.
3. linger가 꺼져 마지막 SSH 종료 후 사용자 서비스가 종료됐다. bootstrap은
   모델 전송 전 runtime 의존성을 확인하고, deploy에서 자기 계정의 linger를
   확인·설정하며 검증 후 B unit만 자동 시작 등록한다.
4. 첫 기동 실패는 서비스를 정지하고 실패 상태를 보존했다. 원인 수정 후
   동일 release·unit·정지 상태를 대조하고 이전 state를 별도 보존한 운영 복구로
   재개했다. 임의의 실패 state를 자동 추측해 수리하는 기능은 아니다.

## 재사용할 명령과 한계

- 최초 준비·배포: [GPU_WORKFLOW.md](GPU_WORKFLOW.md)
- 패키지 전송·TLS 준비의 개별 단계: [GPU_RELEASE.md](GPU_RELEASE.md)
- 완성된 모델 게시·자동 재시작: [GPU_MODEL_PUBLISH.md](GPU_MODEL_PUBLISH.md)
- 서버 쪽 검증·교체·실패 복구: [MODEL_ACTIVATION.md](MODEL_ACTIVATION.md)
- PC HTTPS 검증: [DEPLOYED_VERIFICATION.md](../integration/DEPLOYED_VERIFICATION.md)

완성된 호환 패키지와 신뢰한 manifest SHA를 게시 트리거로 사용한다. 학습 중
임시 파일 변경을 감시해 재시작하지 않는다. 현재 engineering-test 권한을 명시하는
시험용 경로이며 실제 M1의 identity·매핑·임계값·평가 인수는 별도다.

시험 TLS 인증서 만료는 **2026-11-07 10:37:15 UTC**다. 장기 운영에는 적합한
인증서 발급·갱신 절차가 필요하다. A의 학습과 상주 추론은 PRD대로 시간을 분리하며
학습 전 B API를 명시적으로 정지하고 작업 종료 후 승인된 모델을 게시한다.
단일 합성 요청의 처리 시간은 SLO/p95·한국어 탐지 품질을 입증하지 않는다.
