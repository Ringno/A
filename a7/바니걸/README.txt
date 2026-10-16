ComfyUI 자동 생성 실행 안내 (Windows)
=======================================

1. ComfyUI Desktop 또는 ComfyUI 서버를 먼저 실행합니다.
2. ComfyUI/models/checkpoints/ 폴더에
   animeBlendArblendSemi_v12.safetensors 파일이 등록되어 있어야 합니다.
3. RUN.bat을 더블클릭합니다. (Python 3 필요, 추가 pip 설치 불필요)
4. RANDOM(1) 또는 FIXED(2) 시드 방식을 선택합니다.
5. 생성된 이미지는 이 ZIP의 output/ 폴더에 002_정상위.png 형태로 저장됩니다.

- 총 42장, 원본 emotions.json의 번호/장면 프롬프트를 변경하지 않았습니다.
- 원본 workflow.json의 모델, 시드, 샘플러, 배경, 캐릭터 프롬프트를 변경하지 않았습니다.
- 실행 중단 후 다시 켜면 이미 저장된 파일을 건너뛰고 이어서 생성합니다.
- 이미지 다시 만들기: output 폴더에서 기존 파일 삭제, 또는 명령어 'python run.py --overwrite'.
- 테스트 1장: 명령 프롬프트에서 'python run.py --limit 1'.
- 사전 JSON 검사: TEST.bat (ComfyUI를 켜지 않아도 파일 형식 확인 가능).

중요: 선택 검열 커스텀 노드
--------------------------
원본 workflow.json의 출력 구조:
KSampler -> VAEDecode -> GenitalsOnlyContourCensor -> SaveImage

1. ComfyUI에 GenitalsOnlyContourCensor 노드가 설치되어 있으면 원본 그대로 사용합니다.
2. 설치되어 있지 않으면 실행 시 경고와 함께 검열 없이 계속 진행할지 묻습니다.
   Y를 선택한 경우 이번 실행에만 VAEDecode -> SaveImage로 연결합니다.
   N/Enter를 선택하면 중단하고 커스텀 노드를 설치할 수 있습니다.
   원본 workflow.json은 절대로 자동 변경되지 않습니다.
3. 검열 노드 없이 생성하면 이미지에 자동 가림 처리가 되지 않습니다.

검열 영역 검출 오류 시 (이번 버전에서 개선)
-----------------------------------------
- 검열 노드 GenitalsOnlyContourCensor가 설치되어 있어도, 윤곽 인식에 실패하면
  이전에는 그 자리에서 42장 전체 자동 생성이 종료되었습니다.
- 이제 자동으로 두 KSampler 시드를 바꿔 새 이미지를 다시 생성합니다.
  기본 3회 재시도(원본 1회 + 재시도 3회 = 최대 4회)합니다.
- 재시도에도 실패하면 해당 장면은 저장하지 않고, 다음 장면을 계속 생성합니다.
  실패한 파일명은 output/검열실패_목록.txt에 기록됩니다.
- 프로그램을 다시 실행하면 성공 파일은 건너뛰고 실패 장면만 다시 시도합니다.
- 재시도 횟수 조정: python run.py --censor-retries 5
  고정 시드 모드도 재시도할 때만 새로운 시드를 사용합니다.
- 검열 영역 검출에 실패한 원본 이미지를 검열 없이 저장하지 않습니다.
- 모델 누락, 기타 노드 오류 등 검열 영역 검출과 무관한 오류는 기존처럼 종료합니다.

연결 오류 시
-----------
- ComfyUI API 포트: 8188, 8000, 8189, 8190 자동 검색
- 다른 포트일 경우 RUN.bat에서
  rem set "COMFYUI_URL=http://127.0.0.1:8188"
  줄의 rem을 삭제하고 포트를 변경하세요.
- 모델/커스텀 노드가 누락되면 터미널에 상세 오류를 출력합니다.
- Python 설치 확인: 터미널에서 'py -3 --version' 또는 'python --version'.

주의: 이 패키지는 ComfyUI 서버와 해당 체크포인트 모델이 설치된 Windows PC에서
실행하도록 만든 것입니다. 실제 사용자의 GPU 환경에서 이미지 생성 테스트는 불가능합니다.
