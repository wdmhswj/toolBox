import argparse
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText


SUPPORTED_FORMATS = ["mp4", "mkv", "webm", "mov", "avi", "flv"]


def has_ffmpeg() -> bool:
	return shutil.which("ffmpeg") is not None


def pick_codecs(output_ext: str) -> tuple[str, str]:
	ext = output_ext.lower().lstrip(".")
	if ext in {"mp4", "mov", "mkv"}:
		return "libx264", "aac"
	if ext == "webm":
		return "libvpx-vp9", "libopus"
	if ext == "avi":
		return "mpeg4", "mp3"
	if ext == "flv":
		return "flv", "aac"
	return "libx264", "aac"


def build_output_path(input_path: str, target_format: str) -> str:
	p = Path(input_path)
	return str(p.with_suffix(f".{target_format.lower().lstrip('.') }"))


def convert_video(
	input_path: str,
	output_path: str,
	overwrite: bool = False,
	crf: int = 23,
	preset: str = "medium",
	log_callback=None,
) -> int:
	if not has_ffmpeg():
		raise RuntimeError("ffmpeg is not installed or not available in PATH.")

	in_path = Path(input_path)
	out_path = Path(output_path)

	if not in_path.exists():
		raise FileNotFoundError(f"Input file not found: {in_path}")

	if out_path.exists() and not overwrite:
		raise FileExistsError(
			f"Output file already exists: {out_path}. Use overwrite mode to replace it."
		)

	v_codec, a_codec = pick_codecs(out_path.suffix)
	overwrite_flag = "-y" if overwrite else "-n"

	cmd = [
		"ffmpeg",
		overwrite_flag,
		"-i",
		str(in_path),
		"-c:v",
		v_codec,
		"-c:a",
		a_codec,
		"-preset",
		preset,
		"-crf",
		str(crf),
		str(out_path),
	]

	process = subprocess.Popen(
		cmd,
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
		text=True,
		encoding="utf-8",
		errors="replace",
	)

	# ffmpeg progress and messages are typically printed to stderr.
	assert process.stderr is not None
	for line in process.stderr:
		if log_callback:
			log_callback(line.rstrip())

	process.wait()
	return process.returncode


def run_cli(args: argparse.Namespace) -> int:
	input_path = args.input
	output_path = args.output

	if not output_path:
		if not args.format:
			raise ValueError("Either --output or --format must be provided.")
		output_path = build_output_path(input_path, args.format)

	exit_code = convert_video(
		input_path=input_path,
		output_path=output_path,
		overwrite=args.overwrite,
		crf=args.crf,
		preset=args.preset,
		log_callback=print,
	)

	if exit_code == 0:
		print(f"Conversion completed: {output_path}")
	else:
		print(f"Conversion failed. ffmpeg exit code: {exit_code}")
	return exit_code


class ConverterGUI:
	def __init__(self, root: tk.Tk) -> None:
		self.root = root
		self.root.title("Video Format Converter")
		self.root.geometry("760x520")

		self.input_var = tk.StringVar()
		self.output_var = tk.StringVar()
		self.format_var = tk.StringVar(value="mp4")
		self.overwrite_var = tk.BooleanVar(value=True)
		self.crf_var = tk.IntVar(value=23)
		self.preset_var = tk.StringVar(value="medium")
		self.status_var = tk.StringVar(value="Ready")

		self.log_queue: queue.Queue[str] = queue.Queue()
		self.output_auto_managed = True
		self.last_auto_output = ""
		self._build_ui()
		self.format_var.trace_add("write", self._on_format_changed)
		self._poll_logs()

	def _build_ui(self) -> None:
		main = ttk.Frame(self.root, padding=12)
		main.pack(fill=tk.BOTH, expand=True)

		file_frame = ttk.LabelFrame(main, text="File")
		file_frame.pack(fill=tk.X, pady=(0, 10))

		ttk.Label(file_frame, text="Input").grid(row=0, column=0, padx=8, pady=8, sticky=tk.W)
		ttk.Entry(file_frame, textvariable=self.input_var, width=75).grid(
			row=0, column=1, padx=8, pady=8, sticky=tk.EW
		)
		ttk.Button(file_frame, text="Browse", command=self._choose_input).grid(
			row=0, column=2, padx=8, pady=8
		)

		ttk.Label(file_frame, text="Output").grid(row=1, column=0, padx=8, pady=8, sticky=tk.W)
		self.output_entry = ttk.Entry(file_frame, textvariable=self.output_var, width=75)
		self.output_entry.grid(
			row=1, column=1, padx=8, pady=8, sticky=tk.EW
		)
		self.output_entry.bind("<KeyRelease>", self._on_output_typed)
		ttk.Button(file_frame, text="Browse", command=self._choose_output).grid(
			row=1, column=2, padx=8, pady=8
		)
		file_frame.columnconfigure(1, weight=1)

		options_frame = ttk.LabelFrame(main, text="Options")
		options_frame.pack(fill=tk.X, pady=(0, 10))

		ttk.Label(options_frame, text="Target format").grid(
			row=0, column=0, padx=8, pady=8, sticky=tk.W
		)
		ttk.Combobox(
			options_frame,
			textvariable=self.format_var,
			values=SUPPORTED_FORMATS,
			state="readonly",
			width=10,
		).grid(row=0, column=1, padx=8, pady=8, sticky=tk.W)

		ttk.Button(options_frame, text="Build output path", command=self._build_output).grid(
			row=0, column=2, padx=8, pady=8, sticky=tk.W
		)

		ttk.Label(options_frame, text="CRF").grid(row=0, column=3, padx=8, pady=8, sticky=tk.W)
		ttk.Spinbox(options_frame, from_=0, to=51, textvariable=self.crf_var, width=8).grid(
			row=0, column=4, padx=8, pady=8, sticky=tk.W
		)

		ttk.Label(options_frame, text="Preset").grid(row=0, column=5, padx=8, pady=8, sticky=tk.W)
		ttk.Combobox(
			options_frame,
			textvariable=self.preset_var,
			values=[
				"ultrafast",
				"superfast",
				"veryfast",
				"faster",
				"fast",
				"medium",
				"slow",
				"slower",
				"veryslow",
			],
			state="readonly",
			width=10,
		).grid(row=0, column=6, padx=8, pady=8, sticky=tk.W)

		ttk.Checkbutton(options_frame, text="Overwrite output", variable=self.overwrite_var).grid(
			row=0, column=7, padx=8, pady=8, sticky=tk.W
		)

		action_frame = ttk.Frame(main)
		action_frame.pack(fill=tk.X, pady=(0, 10))

		self.convert_button = ttk.Button(action_frame, text="Start Conversion", command=self._start_convert)
		self.convert_button.pack(side=tk.LEFT)
		ttk.Label(action_frame, textvariable=self.status_var).pack(side=tk.RIGHT)

		log_frame = ttk.LabelFrame(main, text="Logs")
		log_frame.pack(fill=tk.BOTH, expand=True)
		self.log_text = ScrolledText(log_frame, height=16, wrap=tk.WORD)
		self.log_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

	def _choose_input(self) -> None:
		path = filedialog.askopenfilename(
			title="Select input video",
			filetypes=[("Video files", "*.mp4 *.mkv *.webm *.mov *.avi *.flv"), ("All files", "*.*")],
		)
		if path:
			self.input_var.set(path)
			# Keep output synced for auto-managed targets when input changes.
			if self.output_auto_managed or self.output_var.get().strip() == self.last_auto_output:
				self._build_output()

	def _choose_output(self) -> None:
		ext = self.format_var.get().strip().lower() or "mp4"
		path = filedialog.asksaveasfilename(
			title="Select output video",
			defaultextension=f".{ext}",
			filetypes=[(f"{ext.upper()} files", f"*.{ext}"), ("All files", "*.*")],
		)
		if path:
			self.output_var.set(path)
			self.output_auto_managed = False

	def _build_output(self) -> None:
		input_path = self.input_var.get().strip()
		target_format = self.format_var.get().strip().lower()
		if not input_path:
			messagebox.showwarning("Missing input", "Please select an input video file first.")
			return
		auto_output = build_output_path(input_path, target_format)
		self.output_var.set(auto_output)
		self.last_auto_output = auto_output
		self.output_auto_managed = True

	def _on_output_typed(self, _event: tk.Event) -> None:
		current = self.output_var.get().strip()
		if current != self.last_auto_output:
			self.output_auto_managed = False

	def _on_format_changed(self, *_args) -> None:
		input_path = self.input_var.get().strip()
		if not input_path:
			return
		if self.output_auto_managed or self.output_var.get().strip() == self.last_auto_output:
			self._build_output()

	def _append_log(self, text: str) -> None:
		self.log_text.insert(tk.END, text + "\n")
		self.log_text.see(tk.END)

	def _enqueue_log(self, text: str) -> None:
		self.log_queue.put(text)

	def _poll_logs(self) -> None:
		while True:
			try:
				line = self.log_queue.get_nowait()
			except queue.Empty:
				break
			self._append_log(line)
		self.root.after(120, self._poll_logs)

	def _set_busy(self, busy: bool) -> None:
		self.convert_button.config(state=tk.DISABLED if busy else tk.NORMAL)

	def _start_convert(self) -> None:
		input_path = self.input_var.get().strip()
		output_path = self.output_var.get().strip()

		if not input_path:
			messagebox.showerror("Invalid input", "Input file path is required.")
			return
		if not output_path:
			messagebox.showerror("Invalid output", "Output file path is required.")
			return

		self._set_busy(True)
		self.status_var.set("Converting...")
		self._append_log("Starting conversion...")

		thread = threading.Thread(
			target=self._convert_worker,
			args=(
				input_path,
				output_path,
				self.overwrite_var.get(),
				int(self.crf_var.get()),
				self.preset_var.get().strip(),
			),
			daemon=True,
		)
		thread.start()

	def _convert_worker(
		self,
		input_path: str,
		output_path: str,
		overwrite: bool,
		crf: int,
		preset: str,
	) -> None:
		try:
			exit_code = convert_video(
				input_path=input_path,
				output_path=output_path,
				overwrite=overwrite,
				crf=crf,
				preset=preset,
				log_callback=self._enqueue_log,
			)
			if exit_code == 0:
				self.root.after(0, lambda: self.status_var.set("Completed"))
				self.root.after(0, lambda: messagebox.showinfo("Done", "Conversion completed successfully."))
			else:
				self.root.after(0, lambda: self.status_var.set("Failed"))
				self.root.after(
					0,
					lambda: messagebox.showerror(
						"Conversion failed", f"ffmpeg exited with code {exit_code}."
					),
				)
		except Exception as exc:  # pylint: disable=broad-except
			self.root.after(0, lambda: self.status_var.set("Error"))
			self.root.after(0, lambda: messagebox.showerror("Error", str(exc)))
			self._enqueue_log(f"Error: {exc}")
		finally:
			self.root.after(0, lambda: self._set_busy(False))


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(
		description="Video format conversion tool with CLI and GUI modes."
	)
	subparsers = parser.add_subparsers(dest="mode")

	cli = subparsers.add_parser("convert", help="Run conversion in command line mode.")
	cli.add_argument("-i", "--input", required=True, help="Input video path.")
	cli.add_argument("-o", "--output", help="Output video path.")
	cli.add_argument(
		"-f",
		"--format",
		choices=SUPPORTED_FORMATS,
		help="Target format if --output is not provided.",
	)
	cli.add_argument("--overwrite", action="store_true", help="Overwrite output if it exists.")
	cli.add_argument("--crf", type=int, default=23, help="Video quality CRF (0-51, lower is better).")
	cli.add_argument(
		"--preset",
		default="medium",
		choices=[
			"ultrafast",
			"superfast",
			"veryfast",
			"faster",
			"fast",
			"medium",
			"slow",
			"slower",
			"veryslow",
		],
		help="Encoding speed/quality preset.",
	)

	subparsers.add_parser("gui", help="Launch graphical interface.")
	return parser


def launch_gui() -> int:
	if not has_ffmpeg():
		messagebox.showerror("Missing ffmpeg", "ffmpeg is not installed or not in PATH.")
		return 1

	root = tk.Tk()
	style = ttk.Style(root)
	if "vista" in style.theme_names():
		style.theme_use("vista")
	app = ConverterGUI(root)
	_ = app
	root.mainloop()
	return 0


def main() -> int:
	parser = build_parser()
	args = parser.parse_args()

	if args.mode == "convert":
		return run_cli(args)

	# Default behavior is GUI mode when no mode is provided.
	return launch_gui()


if __name__ == "__main__":
	try:
		raise SystemExit(main())
	except Exception as err:  # pylint: disable=broad-except
		print(f"Error: {err}", file=sys.stderr)
		raise SystemExit(1)
