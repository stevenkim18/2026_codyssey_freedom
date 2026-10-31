# 읽기 전용 서버 자원·런타임 inventory

`dev`에서 Anaconda `py312`로 실행한다. 실제 서버 접속은 기존 비공개 접속 파일,
검증된 `known_hosts`, 한 번만 사용할 수 있는 password askpass 경로를 사용한다.
호스트 키 자동 수락이나 SSH 설정 우회는 없다.

```sh
python -m deployment.inventory --role gpu --output .cache/inventory-gpu.json
python -m deployment.inventory --role general --output .cache/inventory-general.json
```

`--output`은 선택 사항이다. 지정하면 새 파일만 0600으로 생성하며 기존 경로는
SSH 호출 전에 거부한다. 파일을 생성한 뒤 실행에 실패하면 고정 오류 JSON을
기록한다. 디스크 오류 등으로 기록 자체가 실패하면 빈 파일이 남을 수 있다. stdout은
검증된 JSON 또는 고정 오류 코드 `inventory_failed`만 출력한다. 성공 종료 코드는
0, 실행·스키마·인증 실패는 2다. 원문 SSH stderr, 비밀번호, 환경변수, 프로세스
인자와 임의 프로세스 이름은 출력하지 않는다.

고정된 원격 Python stdlib 코드가 다음을 관찰한다.

- 설정된 역할의 hostname 일치와 Linux 확인, Ubuntu 버전, 시스템 Python 버전
- CPU 개수·load average, `/proc/meminfo`의 전체/사용 가능 메모리(KiB)
- 루트와 접속 사용자 홈 파일시스템의 전체/사용 가능 공간(bytes)
- 접속 사용자의 실제 홈 아래 `anaconda3`, `miniconda3`, `miniforge3`와
  `/opt/conda`만 검사한다. 출력에는 고정 후보 이름 `home_anaconda3`,
  `home_miniconda3`, `home_miniforge3`, `opt_conda`를 사용하고 홈 경로를 복사하지 않는다.
  디렉터리 존재, base/py312 Python 후보 파일, 최대 100개 entry의 환경 개수를
  보고한다. 다른 사용자 홈과 데이터 디렉터리를 탐색하지 않는다. 모든 후보의
  부재가 다른 위치에 Conda가 전혀 없다는 증거는 아니다.
- `/proc/<pid>/comm`을 최대 4096개 PID에 대해 읽고 python/conda/nvidia 범주만
  집계한다. 사라지거나 읽을 수 없는 프로세스는 누락될 수 있다.
- 고정 `/usr/bin/nvidia-smi` 명령을 5초 제한으로 실행해 GPU 모델 범주, 드라이버,
  전체/사용 VRAM(MiB), GPU 사용률을 최대 8개 GPU까지 보고한다.

SSH 전체 제한은 45초다. SSH/GPU 출력 길이는 수집 후 각각 32KiB/8KiB로
검증하며 스트리밍 메모리 상한을 보장하지 않는다. GPU 조회 실패/없음은 `unavailable`, 제한 초과는
`timeout`이며 GPU 미사용이나 드라이버 미설치를 뜻하지 않는다. 미지원 모델은
`other`로 표시한다. 런타임 파일 존재는 설치·실행 가능성 검증이 아니다.
`torch`나 모델을 import하지 않고 conda/Python 후보 실행도 하지 않는다.

이 결과는 설치 계획을 위한 순간 관찰이다. GPU 사용률 0, python 프로세스 0,
낮은 load만으로 다른 담당자의 작업이 없거나 설치·재시작이 안전하다고 판단할
수 없다. CUDA toolkit 버전, 패키지 충돌, 실행 호환성과 GPU 모델 로딩 검증은
별도 단계다. 서버 파일·서비스·패키지·네트워크 설정을 변경하지 않는다.

```sh
python -m pytest deployment/tests/test_inventory.py -q
```

테스트는 합성 SSH·자원 응답과 스키마·권한·고정 명령 검증이다. 실제 SSH 성공과
자원 값은 별도 실행 evidence로 확인해야 한다.
