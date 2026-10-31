# Git 커밋 컨벤션 가이드

> AI PII Gateway 해커톤 팀 공용 (2026-10-31)

## 핵심 원칙

- **이슈/PR 없이** 직접 커밋 & 푸시 (해커톤 속도 우선)
- 커밋 메시지만으로 **누가, 무엇을, 왜** 했는지 알 수 있게 작성
- 담당 영역 태그를 반드시 포함하여 충돌·변경 추적 용이하게

---

## 커밋 메시지 형식

```
<type>(<scope>): <subject>

<body>

<footer>
```

### 예시

```
feat(gateway): 요청 텍스트에서 KR_RRN 마스킹 정책 적용

- R04 정책 우선순위(차단 > 마스킹 > 허용) 구현
- 마스킹 충돌 시 범위 병합 후 뒤쪽부터 치환
- 치환 후 재검사 1회 수행, 잔존 시 차단

담당: B
```

---

## Type (필수)

| Type       | 설명                                | 예시                              |
|------------|-------------------------------------|-----------------------------------|
| `feat`     | 새 기능 추가                        | 마스킹 엔진, SSE 스트림           |
| `fix`      | 버그 수정                           | Unicode 오프셋 계산 오류 수정     |
| `refactor` | 기능 변경 없이 코드 구조 개선       | 검사 파이프라인 함수 분리         |
| `test`     | 테스트 추가 / 수정                  | T03 요청 차단 테스트 추가         |
| `docs`     | 문서 변경                           | API 계약 스키마 업데이트          |
| `data`     | 학습 데이터 / 평가셋 관련           | k-pii-bench 전처리 스크립트       |
| `model`    | 모델 학습 / 평가 / 패키지 관련      | M1 후보 3-epoch 학습 결과         |
| `config`   | 설정 / 환경 변수 / 의존성           | requirements.txt 업데이트         |
| `style`    | 코드 포맷 (동작 변경 없음)          | black / ruff 포매팅 적용          |
| `perf`     | 성능 개선                           | 추론 배치 처리 최적화             |
| `chore`    | 기타 잡무 (빌드, 스크립트 등)       | Dockerfile 수정, 스크립트 추가    |
| `hotfix`   | 시연/데모 직전 긴급 수정            | 시연 중 발견된 차단 누락 긴급 수정|

---

## Scope (필수)

PRD §4 작업 경계에 맞는 폴더/영역 이름을 사용합니다.

| Scope          | 소유자 | dev/ 경로               | 설명                        |
|----------------|--------|-------------------------|-----------------------------|
| `training`     | A      | `dev/training/`         | 학습 코드·데이터 변환       |
| `evaluation`   | A      | `dev/evaluation/`       | 평가·벤치마크               |
| `gateway`      | B      | `dev/gateway/`          | 프록시·어댑터·정책·마스킹   |
| `detector`     | B      | `dev/detector/`         | 검사 API·모델 로딩·추론     |
| `launcher`     | B      | `dev/launcher/`         | 설치·설정·셸 스크립트       |
| `integration`  | B      | `dev/integration/`      | 통합 테스트                 |
| `observability`| C      | `dev/observability/`    | 사건 수집·DB·대시보드·SSE   |
| `contracts`    | 공용   | `dev/contracts/`        | API 계약 스키마             |
| `docs`         | 공용   | `docs/`                 | 문서                        |
| `root`         | 공용   | `/`                     | 루트 설정 파일              |

**여러 scope에 걸치는 경우:** `feat(gateway,detector): ...` 또는 가장 핵심적인 scope 하나만 사용

---

## Subject (필수)

- **한국어 또는 영어** 모두 허용 (팀 편의 우선)
- 명령형으로 작성: "추가", "수정", "제거" (○) / "추가함", "수정했음" (×)
- 50자 이내 권장, 마침표 없음
- **무엇**을 했는지 구체적으로

```
# ✅ 좋은 예
feat(detector): KR_RRN 패턴 정규식 규칙 추가
fix(gateway): 응답 버퍼 메모리 초과 시 차단 누락 수정
test(integration): T04 응답 스트림 원문 미노출 테스트 추가

# ❌ 나쁜 예
fix(gateway): 버그 수정
feat(detector): 업데이트
chore: 작업
```

---

## Body (권장)

- **왜** 이 변경이 필요한지
- **어떻게** 구현했는지 핵심 사항
- PRD 요구사항 ID (R01~R12) 또는 테스트 ID (T01~T12) 참조
- 줄바꿈은 72자 기준

---

## Footer (선택)

- `담당: A` / `담당: B` / `담당: C` — 작업자 표시
- `BREAKING CHANGE:` — 계약 변경이 있을 경우 (PRD §4: 파괴적 변경 시 버전 상승 필요)
- `Refs: R03, T05` — 관련 요구사항/테스트 ID 참조

---

## 브랜치 전략

해커톤 기간 단순화:

```
main ← 시연 가능한 안정 버전
  └── dev ← 통합 개발 브랜치 (기본 작업 브랜치)
        ├── (필요 시) feat/xxx ← 큰 기능 단위 분리
        └── (필요 시) hotfix/xxx ← 긴급 수정
```

- **평소**: `dev` 브랜치에서 직접 작업 & 푸시
- **시연 전**: `dev` → `main` 머지
- 충돌 시: 담당자끼리 직접 소통하여 해결 (Slack/대면)

---

## 🚨 팀원 필독 — 최초 pull 전 필수 작업

`AGENTS.md` 등 개인 LLM 설정 파일이 `.gitignore`에 추가되면서 기존 추적이 해제되었습니다.
**이미 로컬에 해당 파일이 있는 팀원**은 pull 시 삭제될 수 있으므로, 아래 명령어로 백업 후 pull하세요:

```bash
# 1. 백업 → pull → 복원 (한 줄)
cp AGENTS.md AGENTS.md.bak 2>/dev/null; git pull; mv AGENTS.md.bak AGENTS.md 2>/dev/null

# 2. 커밋 템플릿 등록 (최초 1회)
git config --local commit.template .gitmessage
```

이후에는 `.gitignore`가 적용되어 개인 LLM 설정 파일이 자동으로 무시됩니다.

---

## 빠른 참고 — 커밋 전 체크리스트

1. ✅ `type(scope):` 형식을 따랐는가?
2. ✅ subject가 **구체적**인가? ("수정" 대신 "무엇을" 수정했는지)
3. ✅ 비밀 키·개인정보·`.env`가 포함되지 않았는가?
4. ✅ 모델 가중치(`.pt`, `.bin` 등) 대용량 파일이 포함되지 않았는가?
5. ✅ 개인 LLM 설정 파일(`claude.md`, `.cursor/` 등)이 포함되지 않았는가?

