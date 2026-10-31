# Codex CLI 0.166.0: G0 호환 설정 조사

확인일: 2026-10-31. **조사 후보이며 G0 통과 증거가 아니다.** 이 작업에서는
CLI 추론·네트워크 실행, 프로필 변경, 요청 변환을 수행하지 않았다. 사용자 설정
파일을 직접 열지 않았으며 로컬 명령은 `--version`, `--help`, `features list`에 한정했다.
공식 자료는 OpenAI Docs 스킬로 검색 후 해당 페이지를 열어 확인했다.

## 현재 장애와 조사 범위

부모 작업의 격리된 모의 공급자 조사에서 `additional_tools` 3개와 16,000
코드포인트를 넘는 기본 지시문이 관측됐다. 현재 게이트웨이는 PRD에 따라 이를
거절한다. 이 문서 작성 중 해당 요청을 다시 실행하거나 개수·내용을 재측정하지 않았다.
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

## 다음 사이클의 판정 기준

1. 전용 최소 지시문 사용에 대한 제품 동작 변경을 검토한 뒤, 합성 입력·격리 홈·
   모의 공급자·외부 연결 차단 조건에서 설정 조합을 하나씩 확인한다.
2. 원문 대신 필드 이름, 코드포인트 길이, 도구 이름·개수만 수집한다. 먼저 남은
   `additional_tools`의 이름을 확인해야 제거 가능한 기능과 대응시킬 수 있다.
3. 모든 전송 텍스트가 16,000 코드포인트 이하이고 도구 정의·도구 출력·미지원
   상태 참조가 없을 때만 현행 어댑터로 정상/차단/마스킹 왕복을 검증한다.
4. 설정으로 도구를 없앨 수 없다면 G0 실패를 유지한다. 미검사 필드 허용, 지시문
   누락, 무음 축약, 한도 확대는 이 문서의 조사 결론으로 승인하지 않는다.

공식 설정의 존재와 실제 고정 버전의 wire 호환성은 별도다. 현재 조사만으로
완전한 도구 비활성화나 G0 해결을 주장할 수 없다.
