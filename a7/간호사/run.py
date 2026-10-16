# -*- coding: utf-8 -*-
"""ComfyUI API batch runner. Python 3 standard library only."""
import argparse
import copy
import datetime
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKFLOW_PATH = ROOT / "workflow.json"
SCENE_PATH = ROOT / "emotions.json"
OUTPUT_DIR = ROOT / "output"
MARKER = "__AUTO_PROMPT__"
OPTIONAL_CENSOR_TYPE = "GenitalsOnlyContourCensor"
SERVER = None


class CensorDetectionError(RuntimeError):
    """The censor node ran, but it could not identify a safe censoring contour."""


def is_censor_detection_error(messages):
    """Only treat errors originating inside the contour censor as recoverable."""
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, (list, tuple)) or len(message) < 2:
            continue
        kind, details = message[0], message[1]
        if kind == "execution_error" and isinstance(details, dict):
            if details.get("node_type") == OPTIONAL_CENSOR_TYPE:
                return True
    return False


def log_censor_failure(name, error, attempts):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "검열실패_목록.txt"
    date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"[{date}] {name} | {attempts}번 시도 | 검열 영역 검출 실패 | {str(error)[:180]}\n")
    return path


def api_json(path, data=None, timeout=40):
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(SERVER + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8-sig"))


def find_server():
    custom = os.environ.get("COMFYUI_URL", "").strip().rstrip("/")
    candidates = [custom] if custom else [
        "http://127.0.0.1:8188", "http://127.0.0.1:8000",
        "http://127.0.0.1:8189", "http://127.0.0.1:8190"
    ]
    for url in candidates:
        try:
            with urllib.request.urlopen(url + "/system_stats", timeout=2) as resp:
                result = json.load(resp)
            if isinstance(result, dict) and "system" in result:
                return url
            # Different ComfyUI releases may return different stats fields.
            if isinstance(result, dict):
                return url
        except (OSError, ValueError, urllib.error.URLError):
            continue
    locations = ", ".join(candidates)
    raise RuntimeError(
        "ComfyUI 서버에 연결할 수 없습니다. 먼저 ComfyUI를 실행해 주세요.\n"
        "확인한 주소: " + locations + "\n"
        "포트를 직접 지정하려면 RUN.bat에서 COMFYUI_URL을 수정할 수 있습니다."
    )


def load_scenes():
    if not SCENE_PATH.is_file():
        raise RuntimeError("emotions.json 파일이 없습니다.")
    data = json.loads(SCENE_PATH.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list) or not data:
        raise RuntimeError("emotions.json에는 [파일이름, 프롬프트] 형태의 장면 목록이 있어야 합니다.")
    scenes = []
    used = set()
    for i, item in enumerate(data, 1):
        if not isinstance(item, list) or len(item) != 2 or not all(isinstance(v, str) for v in item):
            raise RuntimeError(f"emotions.json {i}번째 항목은 ['001_이름','프롬프트'] 형식이어야 합니다.")
        name, prompt = item
        if not name or any(ch in name for ch in '<>:"/\\|?*') or name.endswith((" ", ".")):
            raise RuntimeError(f"잘못된 출력 파일 이름: {name!r}")
        if name.lower() in used:
            raise RuntimeError(f"중복된 출력 파일 이름: {name}")
        used.add(name.lower())
        scenes.append((name, prompt))
    return scenes


def load_workflow():
    if not WORKFLOW_PATH.is_file():
        raise RuntimeError("workflow.json 파일이 없습니다.")
    data = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not data or not all(isinstance(node, dict) and "class_type" in node for node in data.values()):
        raise RuntimeError("workflow.json은 ComfyUI의 API Format JSON이어야 합니다.")
    return data


def find_positive(workflow):
    nodes = [(i, n) for i, n in workflow.items() if n.get("class_type") == "CLIPTextEncode"]
    for i, n in nodes:
        if MARKER in n.get("inputs", {}).get("text", ""):
            return i
    for i, n in nodes:
        if "positive" in str(n.get("_meta", {}).get("title", "")).lower():
            return i
    if "3" in workflow and workflow["3"].get("class_type") == "CLIPTextEncode":
        return "3"
    raise RuntimeError("긍정 프롬프트(CLIPTextEncode)를 찾을 수 없습니다.")


def find_save(workflow):
    matches = [i for i, n in workflow.items() if n.get("class_type") == "SaveImage"]
    if not matches:
        raise RuntimeError("이미지를 파일로 저장하는 SaveImage 노드가 없습니다.")
    return matches[0]


def find_seeds(workflow):
    found = []
    for i, node in workflow.items():
        t = node.get("class_type", "")
        inputs = node.get("inputs", {})
        if "KSampler" in t and "seed" in inputs:
            found.append((i, "seed"))
        elif "KSampler" in t and "noise_seed" in inputs:
            found.append((i, "noise_seed"))
        elif "sampler" in t.lower() and "seed" in inputs:
            found.append((i, "seed"))
    if not found:
        raise RuntimeError("KSampler 시드 입력을 찾을 수 없습니다.")
    return found


def patch_missing_censor(workflow, missing):
    """Remove optional censor ONLY after clear consent; connect SaveImage to its image input."""
    for node_id, node in list(workflow.items()):
        if node.get("class_type") != OPTIONAL_CENSOR_TYPE:
            continue
        source = node.get("inputs", {}).get("images")
        if not isinstance(source, list) or len(source) != 2 or str(source[0]) not in workflow:
            raise RuntimeError("검열 노드가 연결된 원본 이미지를 찾을 수 없어 자동 우회가 불가능합니다.")
        for dst_id, dst_node in workflow.items():
            if dst_id == node_id:
                continue
            inputs = dst_node.get("inputs", {})
            for key, value in inputs.items():
                if isinstance(value, list) and len(value) == 2 and str(value[0]) == node_id:
                    # Censor node has one image input and a single image output.
                    inputs[key] = list(source)
        del workflow[node_id]
        print(f"[INFO] 누락된 선택 노드 {node_id}({OPTIONAL_CENSOR_TYPE})를 우회했습니다.")
    return workflow


def check_nodes_and_model(workflow, allow_uncensored):
    available = api_json("/object_info", timeout=30)
    if not isinstance(available, dict):
        raise RuntimeError("ComfyUI 노드 정보를 읽을 수 없습니다.")
    required = {node["class_type"] for node in workflow.values()}
    missing = required - set(available)
    if missing == {OPTIONAL_CENSOR_TYPE}:
        print("\n[주의] ComfyUI에 'GenitalsOnlyContourCensor' 커스텀 노드가 없습니다.")
        print("       원본 workflow.json은 변경하지 않고, 이번 실행에서만 검열을 생략할 수 있습니다.")
        print("       검열을 생략하면 출력 이미지에 자동 가림 처리가 되지 않습니다.")
        if allow_uncensored:
            print("       --allow-uncensored 옵션으로 계속 진행합니다.")
        else:
            ans = input("검열 없이 계속할까요? [y/N]: ").strip().lower()
            if ans not in {"y", "yes", "ㅛ"}:
                raise RuntimeError("중단했습니다. 해당 커스텀 노드를 설치하거나 검열 없이 계속하도록 선택하세요.")
        patch_missing_censor(workflow, missing)
        missing = set()
    if missing:
        raise RuntimeError("설치되지 않은 필수 ComfyUI 노드: " + ", ".join(sorted(missing)))

    for node in workflow.values():
        if node.get("class_type") != "CheckpointLoaderSimple":
            continue
        desired = node.get("inputs", {}).get("ckpt_name", "")
        possible = (available.get("CheckpointLoaderSimple", {})
                    .get("input", {}).get("required", {}).get("ckpt_name", []))
        names = possible[0] if possible and isinstance(possible[0], list) else None
        if names is not None and desired not in names:
            raise RuntimeError(
                "체크포인트가 ComfyUI에 등록되어 있지 않습니다: " + repr(desired) + "\n"
                "ComfyUI/models/checkpoints에 파일을 넣고 ComfyUI를 재시작하거나 "
                "workflow.json의 ckpt_name을 설치된 모델 이름으로 수정하세요.\n"
                "현재 선택 가능한 예시: " + ", ".join(map(str, names[:10]))
            )
    return workflow


def queue_workflow(workflow):
    payload = json.dumps({"prompt": workflow}, ensure_ascii=False).encode("utf-8")
    try:
        data = api_json("/prompt", data=payload, timeout=60)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ComfyUI 작업 요청 실패 (HTTP {exc.code}): {body[:2500]}") from exc
    if "prompt_id" not in data:
        raise RuntimeError("ComfyUI에서 prompt_id를 받지 못했습니다: " + str(data)[:2500])
    return str(data["prompt_id"])


def wait_image(prompt_id, save_id, timeout_minutes=20):
    deadline = time.monotonic() + timeout_minutes * 60
    consecutive_errors = 0
    while time.monotonic() < deadline:
        try:
            response = api_json("/history/" + urllib.parse.quote(prompt_id), timeout=20)
            consecutive_errors = 0
        except (urllib.error.URLError, ValueError, TimeoutError) as exc:
            consecutive_errors += 1
            if consecutive_errors >= 20:
                raise RuntimeError("ComfyUI 작업 확인이 반복 실패했습니다: " + str(exc)) from exc
            time.sleep(2)
            continue
        info = response.get(prompt_id, {})
        if info:
            image_list = info.get("outputs", {}).get(save_id, {}).get("images", [])
            if image_list:
                return image_list[0]
            status = info.get("status", {})
            if status.get("status_str") == "error":
                messages = status.get("messages", [])
                details = json.dumps(messages, ensure_ascii=False)
                if is_censor_detection_error(messages):
                    raise CensorDetectionError("검열 영역을 확정하지 못했습니다: " + details[:800])
                raise RuntimeError("ComfyUI 생성 오류: " + details[:2500])
            if status.get("completed") is True:
                raise RuntimeError("작업은 완료됐지만 SaveImage의 결과가 없습니다. 노드 연결을 확인하세요.")
        time.sleep(2)
    raise TimeoutError("20분 동안 완료되지 않았습니다. ComfyUI 화면에서 오류와 남은 작업을 확인하세요.")


def save_image(image, name):
    params = urllib.parse.urlencode({
        "filename": image["filename"],
        "subfolder": image.get("subfolder", ""),
        "type": image.get("type", "output")
    })
    ext = Path(image["filename"]).suffix.lower() or ".png"
    destination = OUTPUT_DIR / (name + ext)
    partial = destination.with_suffix(destination.suffix + ".part")
    try:
        with urllib.request.urlopen(SERVER + "/view?" + params, timeout=120) as resp, partial.open("wb") as output:
            while True:
                block = resp.read(256 * 1024)
                if not block:
                    break
                output.write(block)
        if partial.stat().st_size == 0:
            raise RuntimeError("ComfyUI에서 빈 이미지를 받았습니다.")
        partial.replace(destination)
    finally:
        if partial.exists():
            partial.unlink()
    return destination


def main():
    parser = argparse.ArgumentParser(description="ComfyUI 자동 생성 - 장면 일괄 생성")
    parser.add_argument("--check", action="store_true", help="JSON 파일/노드 연결만 확인하고 종료")
    parser.add_argument("--mode", choices=["random", "fixed"], help="시드 방식 (기본 실행 시 선택)")
    parser.add_argument("--limit", type=int, default=0, help="테스트 생성용 앞 N장 제한; 0은 전체")
    parser.add_argument("--overwrite", action="store_true", help="이미 생성한 파일도 다시 생성")
    parser.add_argument("--censor-retries", type=int, default=3,
                        help="검열 영역 인식 오류 시 새 시드로 재시도할 횟수 (기본 3회)")
    parser.add_argument("--allow-uncensored", action="store_true", help="검열 노드가 없을 때 확인 없이 우회")
    args = parser.parse_args()

    print("=" * 61)
    print("ComfyUI 자동 이미지 생성 / RANDOM 또는 FIXED SEED")
    print("=" * 61)
    scenes = load_scenes()
    workflow = load_workflow()
    positive_id = find_positive(workflow)
    save_id = find_save(workflow)
    seed_inputs = find_seeds(workflow)
    save_link = workflow[save_id].get("inputs", {}).get("images")
    if not isinstance(save_link, list) or len(save_link) != 2 or str(save_link[0]) not in workflow:
        raise RuntimeError("SaveImage 노드가 이미지 생성 노드에 제대로 연결되어 있지 않습니다.")
    base_prompt = workflow[positive_id]["inputs"].get("text", "")
    if not isinstance(base_prompt, str):
        raise RuntimeError("CLIPTextEncode의 text 입력이 문자열이 아닙니다.")

    print(f"[OK] 장면 {len(scenes)}개 / 긍정 노드 {positive_id} / 저장 노드 {save_id}")
    print("[OK] 시드 노드: " + ", ".join(f"{i}:{k}" for i, k in seed_inputs))
    if args.check:
        print("[OK] workflow.json과 emotions.json 형식 및 연결 검사 성공")
        print("[참고] 실제 모델/커스텀 노드는 ComfyUI에 접속한 뒤 확인할 수 있습니다.")
        return
    if args.limit < 0:
        raise RuntimeError("--limit 값은 0 이상이어야 합니다.")
    if args.censor_retries < 0 or args.censor_retries > 20:
        raise RuntimeError("--censor-retries 값은 0~20 사이여야 합니다.")

    global SERVER
    SERVER = find_server()
    print("[OK] ComfyUI API 연결:", SERVER)
    workflow = check_nodes_and_model(workflow, args.allow_uncensored)

    mode = args.mode
    if not mode:
        print("\n시드 선택: 1 = 장면마다 랜덤 (추천), 2 = workflow.json 고정 시드")
        choice = input("선택 [1/2, 기본 1]: ").strip()
        mode = "fixed" if choice == "2" else "random"
    print("[OK] 시드 방식:", mode)
    if MARKER not in base_prompt:
        print("[알림] 마커가 없어 장면 프롬프트를 긍정 프롬프트 끝에 추가합니다.")

    chosen = scenes[:args.limit] if args.limit else scenes
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    total = len(chosen)
    saved = 0
    skipped = 0
    censor_failed = 0
    for i, (name, scene_prompt) in enumerate(chosen, 1):
        if not args.overwrite and any((OUTPUT_DIR / (name + ext)).exists() for ext in (".png", ".jpg", ".jpeg", ".webp")):
            print(f"[{i}/{total}] 이미 존재하여 건너뜀: {name}")
            skipped += 1
            continue
        job = copy.deepcopy(workflow)
        scene_prompt = scene_prompt.strip()
        if MARKER in base_prompt:
            job[positive_id]["inputs"]["text"] = base_prompt.replace(MARKER, scene_prompt, 1)
        else:
            job[positive_id]["inputs"]["text"] = base_prompt.rstrip() + "\n" + scene_prompt
        job[save_id]["inputs"]["filename_prefix"] = name
        if mode == "random":
            for node_id, seed_key in seed_inputs:
                job[node_id]["inputs"][seed_key] = secrets.randbelow(2**63)
        print(f"[{i}/{total}] 생성 중: {name}", flush=True)
        print("     seeds:", ", ".join(str(job[n]["inputs"][k]) for n, k in seed_inputs), flush=True)
        for attempt in range(args.censor_retries + 1):
            prompt_id = queue_workflow(job)
            try:
                image = wait_image(prompt_id, save_id)
            except CensorDetectionError as exc:
                if attempt >= args.censor_retries:
                    censor_failed += 1
                    path = log_censor_failure(name, exc, attempt + 1)
                    print(f"     [검열 실패] {attempt + 1}회 시도 후 이 장면만 건너뜀: {name}", flush=True)
                    print(f"     [기록] {path.name} / 나머지 장면 계속 생성", flush=True)
                    break
                # Regenerate with new seeds: do NOT bypass the censor or save a raw image.
                for node_id, seed_key in seed_inputs:
                    job[node_id]["inputs"][seed_key] = secrets.randbelow(2**63)
                print(f"     [검열 인식 실패] 새 시드로 다시 생성 ({attempt + 1}/{args.censor_retries})", flush=True)
                print("     seeds:", ", ".join(str(job[n]["inputs"][k]) for n, k in seed_inputs), flush=True)
                continue
            result = save_image(image, name)
            saved += 1
            print("     저장 완료:", result.name, flush=True)
            break
    print(f"\n[완료] 새로 생성 {saved}장 / 기존 파일 건너뜀 {skipped}장 / 검열 실패 {censor_failed}장")
    print("결과 폴더:", OUTPUT_DIR)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n사용자가 중단했습니다.")
        sys.exit(130)
    except Exception as error:
        print("\n[오류]", error)
        sys.exit(1)
