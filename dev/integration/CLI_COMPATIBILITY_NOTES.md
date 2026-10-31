# Codex CLI 0.166.0: G0 호환 설정 조사

확인일: 2026-10-31. 아래는 **격리 설정 조사 기록**이다. 초기 문서 조사는
읽기 전용 명령에 한정했으며, 재개 사이클에서는 승인된 격리 CLI·loopback 모의
공급자 실험을 수행했다. 실제 생성 모델이나 실제 API 키는 사용하지 않았다.
사용자 설정 파일을 직접 열지 않았고 제품 보호 프로필·게이트웨이 어댑터는 변경하지 않았다.
공식 자료는 OpenAI Docs 스킬로 검색 후 해당 페이지를 열어 확인했다.

## 현재 장애와 조사 범위

초기 격리 조사에서 `additional_tools`와 16,000 코드포인트를 넘는 지시문이 관측됐다.
재개 실험은 아래 표처럼 실제 leaf 도구 개수와 문자열 길이를 분리해 측정했다.
현재 기본 어댑터는 이 CLI envelope를 거절한다. 이는 구현의 지원 범위이며,
**PRD가 도구 정의 0개를 필수로 요구한다는 의미는 아니다.** PRD §2는 정의가
불가피한 CLI의 경우 미지원 도구 출력을 전달 전에 차단하고 자동 실행을 끄는
방식을 허용한다. 정의의 설명·문법·스키마를 미검사 전달하는 예외는 아니다.
`codex --version` 출력은 `codex-cli 0.166.0`이었다.

`features list`는 기능 이름과 상태를 확인하는 근거다. 이 명령의 현재 값이 기존
격리 프로필의 실제 적용값이나 각 `additional_tools`의 발생 원인을 증명하지 않는다.
명령은 PATH 별칭 생성 권한 경고를 냈지만 종료 코드는 0이었다.

## 공식 지원이 확인된 설정

`model_instructions_file`은 CLI 기본 지시문을 파일로 대체하는 공식 설정이다.
짧은 전용 지시문을 사용하도록 **CLI를 명시적으로 구성하는 후보**가 있다. 이미
생성된 요청에서 게이트웨이가 지시문을 삭제·잘라내는 접근과 구분해야 한다.
이 설정만으로 모든 관리 지시문·환경 문맥·도구 정의가 사라진다는 보장은 찾지 못했다.
[설정 참조](https://learn.chatgpt.com/docs/config-file/config-reference)

다음은 후속 격리 실험에서 검토할 값이다. 현재 보호 프로필에 적용하지 않았다.
기존 필수 공급자·토큰·loopback 설정을 유지한 채 검증해야 한다.

```toml
model_instructions_file = "/absolute/path/to/reviewed-text-only-instructions.md"
features.shell_tool = false
features.apps = false
features.multi_agent = false
agents.enabled = false
tools.view_image = false
web_search = "disabled"
personality = "none"
```

명령 실행·앱·다중 에이전트·이미지 보기에는 각각의 제어 항목이 있다.
공식 문서는 `features.unified_exec`의 사용자 opt-out을 무시한다고 설명하므로,
그 값만 `false`로 설정해 도구가 제거됐다고 판정하면 안 된다.
[설정 참조](https://learn.chatgpt.com/docs/config-file/config-reference)

`web_search="disabled"`는 웹 검색을 끄고 `personality="none"`은 기본 성격
지시문 부분을 줄이는 설정이다. 사용자 정의 기본 지시문 자체를 바꾸지는 않는다.
[기본 설정](https://learn.chatgpt.com/docs/config-file/config-basic)

`-c`는 단일 실행의 TOML 설정을 명시하는 공식 수단이다. 현재 보호 실행기가
필수 설정을 고정하므로 실험값은 검토된 생성 경로에 추가해야 하며, 사용자 임의
`-c` 허용으로 구현해서는 안 된다.
[CLI 옵션](https://learn.chatgpt.com/docs/cli/reference)

## 로컬 기능 목록에서 드러난 한계

`rtk proxy codex features list`에서 `shell_tool`, `unified_exec`, `apps`,
`multi_agent`, `view_image`, `image_generation`, `sleep_tool`, `goals`는 stable로
노출됐고 이 명령의 값은 true였다. `apply_patch_freeform`은 removed/false였다.
따라서 기존 `features.apply_patch_freeform=false`를 실제 도구 제거의 증거로
삼으면 안 된다. `image_generation`, `sleep_tool`, `goals`를 끄는 추가 후보는
기능 이름이 존재한다는 사실만 확인했으며 요청 스키마 효과를 검증하지 않았다.

공식 커스텀 하위 에이전트 문서는 `features.plugins=false`,
`skills.bundled.enabled=false`, `skills.include_instructions=false` 등을 설명한다.
이 설명을 루트 CLI의 동일한 동작 보장으로 확장하지 않는다. 별도 호환 확인 대상이다.
[하위 에이전트 설정](https://learn.chatgpt.com/docs/agent-configuration/subagents)

## 격리된 실제 CLI wire 실험 결과

`probe_cli.py`는 임시 Codex 홈, 명시적인 `-c` loopback provider와 합성 토큰을
사용한다. child 시작 전에 거절 프록시를 열고 대·소문자 HTTP/HTTPS/ALL_PROXY를
그 주소로 고정한다. localhost만 NO_PROXY에 둔다. 원문·API 키·stdout/stderr
본문은 보고하지 않고 구조·도구 이름·길이·종료 코드만 반환한다. 환경 프록시는
이를 따르는 연결에 대한 방어이며 운영체제 수준의 전체 네트워크 격리 증거는 아니다.

아래 11조합은 모두 모의 공급자 요청 1회, CLI 종료 0, 합성 SAFE_PROBE 수신을
확인했다. 도구 정의를 받는 모의 공급자가 직접 SSE를 반환한 결과이므로 실제
보호 게이트웨이의 요청 수용·PII 통제 성공으로 집계하지 않는다.

| variant | message/instructions 코드포인트 | namespace 정의 수 | leaf 도구 수 | 프록시가 거절한 외부 시도 |
|---|---:|---:|---:|---:|
| baseline | 37,785 | 3 | 11 | 3 |
| minimal | 16,098 | 3 | 11 | 3 |
| agents_off | 13,427 | 2 | 5 | 3 |
| images_off | 16,098 | 3 | 11 | 3 |
| sleep_goals_off | 16,098 | 2 | 10 | 3 |
| all_off | 1,895 | 1 | 4 | 1 |
| user_input_off | 1,895 | 1 | 2 | 1 |
| code_mode_off | 1,895 | 1 | 2 | 1 |
| direct_tools_off | 1,895 | 1 | 2 | 1 |
| host_table_off | 1,895 | 1 | 2 | 1 |
| no_discovery | 1,895 | 1 | 2 | 0 |

`minimal` 이후는 82코드포인트의 전용 합성 기본 지시문을 명시했다. 제품 적용 시
이 기본 동작 변경을 문서화해야 한다. `agents_off`는 collaboration 도구를,
`sleep_goals_off`는 clock.sleep을 제거했다. `all_off`는 프로브에 열거한 플러그인·
skill·브라우저 등의 설정도 끈 실험 이름이며 모든 기능 제거를 보장하는 이름은 아니다.
설정이 CLI에 모두 인식됐다는 주장 대신 실제 wire 변화를 근거로 삼는다.

공식 JSON 설정 스키마의 `tools.experimental_request_user_input.enabled=false`를
추가한 `user_input_off`에서 동기·비동기 질문 도구가 제거됐다. Code Mode boolean/
enabled-table 및 incremental_tools 설정은 남은 exec/wait 정의를 제거하지 못했다.
`no_discovery`는 모델 discovery·cyber discovery·analytics·feedback도 껐다.
[공식 JSON 설정 스키마](https://learn.chatgpt.com/docs/config-schema.json)

최종 `no_discovery` 재관측에서 남은 구조는 다음과 같다. 설명·문법 원문은 기록하지 않았다.

- namespace: `type,name,description,tools`; 이름은 `functions`, description 길이는 0.
- `functions.exec`: custom 도구. `type,name,description,format` 필드이며 설명 7,406cp.
  format은 grammar이며 `type,syntax,definition` 문자열의 길이는 각각 7, 4, 177cp.
- `functions.wait`: function 도구. `type,name,description,strict,parameters` 필드이며 설명 769cp.
  parameters는 object, additionalProperties=false, required=`cell_id`다.
  properties는 `cell_id:string`, `max_tokens:number`, `terminate:boolean`, `yield_time_ms:number`다.

namespace와 leaf의 문자열 값은 합산 8,665cp, message와 합산 10,560cp였다.
이 합계는 JSON key 이름과 root telemetry 문자열은 제외한다. 실제 어댑터는
모든 전달 문자열과 동적 key를 독자적으로 추출·합산하고 16,000 한도를 적용해야 한다.
root `client_metadata/include/prompt_cache_key`, message `id/type`, 불가피한
`additional_tools`에 대한 타입·필드 계약도 별도로 필요하다.

재현 명령(저장소 루트, Anaconda py312):

```sh
rtk proxy /Users/oliverjoo/Dev/Anaconda/anaconda3/envs/py312/bin/python dev/integration/probe_cli.py --variant no_discovery
```

프로브 helper/driver는 실패 테스트 후 구현했으며, 합성 schema·grammar의 모든
문자열 위치/길이 보고, child의 키 미상속, 프록시 준비 순서, 오류 정리·본문 미기록을
기존 pytest 경로로 검증한다. 실제 exec 정의는 custom grammar이므로 일반 JSON
function fixture 하나만으로 두 도구를 대표해서는 안 된다.

## 다음 사이클의 판정 기준

1. 전용 최소 지시문 사용에 대한 제품 동작 변경을 검토한 뒤, 합성 입력·격리 홈·
   모의 공급자·외부 연결 차단 조건에서 설정 조합을 하나씩 확인한다.
2. 원문 대신 필드 이름, 코드포인트 길이, 도구 이름·개수만 수집한다. 먼저 남은
   `additional_tools`의 이름을 확인해야 제거 가능한 기능과 대응시킬 수 있다.
3. 불가피한 정의를 지원한다면 허용한 도구·namespace·schema 구조를 엄격 검증하고
   설명·문법·모든 schema 문자열/key를 검사한다. 자동 실행을 끄고 공급자
   `tool_choice=none`을 강제하며 모든 도구 출력·암호화 이력·미지원 상태 참조를 차단한다.
4. 전체 전송 텍스트 16,000 한도 안에서 실제 보호 경로의 정상/차단/마스킹 왕복을
   검증한 뒤 G0를 판정한다. 미검사 필드 허용, 지시문 누락, 무음 축약, 한도 확대는
   이 문서의 조사 결론으로 승인하지 않는다.

공식 설정의 존재와 실제 고정 버전의 wire 호환성은 별도다. 현재 조사에서는
완전한 도구 비활성화를 찾지 못했고, 불가피한 정의를 검사하는 호환 어댑터의 실제
왕복 검증도 수행하지 않았다. 따라서 이 조사 단계만으로 G0 해결을 주장할 수 없다.

## 통합 이후 검증

부모 통합 단계에서 `gateway.cli_request`와 검토된 텍스트 전용 프로필을 연결했다.
`integration/run_smoke.py`는 실제 고정 CLI의 정상·요청/응답 마스킹·차단·도구 출력
거절·검사기 장애를 모의 검사기/공급자와 검증한다. HTTP 6개와 CLI 7개를 합친
13개 검증이 통과했다. 도구 정의는 문자열·키·숫자를 포함해 검사하고 `tool_choice=none`
으로 전송하며, 사용자 요청 내용의 생략이나 합산 한도 확대는 하지 않는다.
이는 원격 OpenAI·실제 GPU·전체 Codex 도구 실행 지원의 완료 증거가 아니다.
