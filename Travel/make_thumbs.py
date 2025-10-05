#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import concurrent.futures
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}
DEFAULT_FONT_MAC = "/Library/Fonts/Arial Bold.ttf"  # поменяйте при необходимости


def check_deps():
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            sys.exit(f"[!] {tool} не найден. Установите FFmpeg (macOS: brew install ffmpeg).")


def list_videos(folder: Path):
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS]
    files.sort()
    return files


def ffprobe_duration(path: Path) -> float:
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            stderr=subprocess.STDOUT
        )
        return max(0.0, float(out.decode().strip()))
    except Exception:
        return 0.0


def run(cmd: list):
    # Запускаем ffmpeg/ffprobe, печатаем краткую ошибку при падении
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, ""
    except subprocess.CalledProcessError as e:
        return False, f"Command failed ({e.returncode}): {' '.join(map(str, cmd))}"
    except Exception as e:
        return False, f"Error: {e}"


def title_from_filename(path: Path) -> str:
    base = path.stem.replace("_", " ")
    return base


def build_smart_cmd(inp: Path, out: Path, width: int, height: int):
    return [
        "ffmpeg", "-y", "-v", "error", "-i", str(inp),
        "-vf", f"thumbnail=600,scale={width}:{height}",
        "-frames:v", "1", str(out)
    ]


def build_middle_cmd(inp: Path, out: Path, width: int, height: int, percent: float):
    dur = ffprobe_duration(inp)
    ts = max(0.0, dur * percent) if dur > 0 else 0.0
    return [
        "ffmpeg", "-y", "-v", "error", "-ss", f"{ts:.3f}", "-i", str(inp),
        "-frames:v", "1", "-vf", f"scale={width}:{height}", str(out)
    ]


def build_styled_cmd(inp: Path, out: Path, width: int, height: int,
                     canvas_h: int, font: str, logo: str | None, title: str):
    # Пишем заголовок во временный файл (без экранирования спецсимволов)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
    tmp.write(title.encode("utf-8"))
    tmp.close()

    vf_common = (
        f"thumbnail=600,scale={width}:{height},"
        f"pad={width}:{canvas_h}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"drawbox=x=0:y=oh-140:w=ow:h=140:color=black@0.55:t=fill,"
        f"drawtext=fontfile='{font}':textfile='{tmp.name}':x=40:y=h-70:"
        f"fontsize=48:fontcolor=white:borderw=2:bordercolor=black@0.9"
    )

    if logo:
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", str(inp), "-i", str(logo),
            "-filter_complex", f"[0:v]{vf_common}[bg];[bg][1:v]overlay=20:20",
            "-frames:v", "1", str(out)
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-v", "error", "-i", str(inp),
            "-vf", vf_common, "-frames:v", "1", str(out)
        ]
    return cmd, tmp.name  # вернём путь к temp-файлу, чтобы удалить после


def build_grid_cmd(inp: Path, out: Path, width: int, tile: str):
    # tile = "3x3"
    try:
        cols, rows = tile.lower().split("x")
        cols, rows = int(cols), int(rows)
        assert cols > 0 and rows > 0
    except Exception:
        raise ValueError(f"Неверный --tile: {tile}")

    total = cols * rows
    cellw = max(1, width // cols)
    dur = ffprobe_duration(inp)
    # Подбираем FPS так, чтобы было достаточно кадров даже для коротких роликов
    fps = 1 if dur <= 0 else max(1, math.ceil(total / dur))

    return [
        "ffmpeg", "-y", "-v", "error", "-i", str(inp),
        "-vf", f"fps={fps},scale={cellw}:-2,tile={cols}x{rows}",
        "-frames:v", str(total),
        str(out)
    ]


def process_one(path: Path, outdir: Path, mode: str, width: int, height: int,
                percent: float, canvas_h: int, font: str, logo: str | None, tile: str):
    base = path.stem
    if mode == "grid":
        out_path = outdir / f"{base}_grid.jpg"
    else:
        out_path = outdir / f"{base}.jpg"

    if out_path.exists():
        return True, str(path), "skip (exists)"

    if mode == "smart":
        cmd = build_smart_cmd(path, out_path, width, height)
        ok, msg = run(cmd)
        return ok, str(path), msg

    elif mode == "middle":
        cmd = build_middle_cmd(path, out_path, width, height, percent)
        ok, msg = run(cmd)
        return ok, str(path), msg

    elif mode == "styled":
        title = title_from_filename(path)
        cmd, tmpfile = build_styled_cmd(path, out_path, width, height, canvas_h, font, logo, title)
        try:
            ok, msg = run(cmd)
            return ok, str(path), msg
        finally:
            try:
                os.remove(tmpfile)
            except Exception:
                pass

    elif mode == "grid":
        cmd = build_grid_cmd(path, out_path, width, tile)
        ok, msg = run(cmd)
        return ok, str(path), msg

    else:
        return False, str(path), f"unknown mode: {mode}"


def parse_args():
    p = argparse.ArgumentParser(
        description="Batch видео-превью на FFmpeg (smart/middle/styled/grid).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    p.add_argument("-i", "--input", type=Path, default=Path("."), help="Папка с видео")
    p.add_argument("-o", "--out", type=Path, default=Path("thumbs"), help="Папка вывода")
    p.add_argument("-m", "--mode", choices=["smart", "middle", "styled", "grid"], default="smart",
                   help="Режим генерации превью")
    p.add_argument("-w", "--width", type=int, default=1280, help="Ширина превью/холста")
    p.add_argument("-H", "--height", type=int, default=-2, help="Высота (−2 = авто по пропорции)")
    p.add_argument("-p", "--percent", type=float, default=0.30, help="Доля длительности для middle (0..1)")
    p.add_argument("--canvas-height", type=int, default=720, help="Высота холста для styled")
    p.add_argument("--font", type=str, default=DEFAULT_FONT_MAC, help="Путь к .ttf для drawtext (styled)")
    p.add_argument("--logo", type=str, default="", help="PNG логотип (styled, опционально)")
    p.add_argument("--tile", type=str, default="3x3", help="Сетка для grid (например, 3x3, 4x3)")
    p.add_argument("-j", "--jobs", type=int, default=1, help="Параллельных задач")
    return p.parse_args()


def main():
    args = parse_args()
    check_deps()

    in_dir: Path = args.input.resolve()
    out_dir: Path = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    files = list_videos(in_dir)
    if not files:
        print("Видео не найдены (расширения: *.mp4 *.mov *.mkv *.avi *.m4v *.webm).")
        return

    logo = args.logo if args.logo else None

    print(f"Найдено файлов: {len(files)}")
    print(f"Режим: {args.mode}, размер: {args.width}x{args.height}, выход: {out_dir}")
    if args.mode == "middle":
        print(f"Процент длительности: {args.percent:.2f}")
    if args.mode == "styled":
        print(f"Шрифт: {args.font}")
        if logo:
            print(f"Логотип: {logo}")
        print(f"Высота холста: {args.canvas_height}")
    if args.mode == "grid":
        print(f"Сетка: {args.tile}")

    tasks = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        for path in files:
            tasks.append(ex.submit(
                process_one, path, out_dir, args.mode, args.width, args.height,
                args.percent, args.canvas_height, args.font, logo, args.tile
            ))

        done = 0
        errors = 0
        for fut in concurrent.futures.as_completed(tasks):
            ok, name, msg = fut.result()
            done += 1
            prefix = "✓" if ok else "✗"
            if msg and msg != "skip (exists)":
                print(f"[{prefix}] {Path(name).name}: {msg}")
            else:
                print(f"[{prefix}] {Path(name).name}")
            if not ok:
                errors += 1

    print(f"Готово. Успешно: {len(files) - errors}, ошибок: {errors}. Превью в: {out_dir}")


if __name__ == "__main__":
    main()
