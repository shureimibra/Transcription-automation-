import argparse
import os
import sys

tools_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(tools_dir, '..'))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

try:
    from core.services.whisper_training import export_faster_whisper_ready_checkpoint
except ModuleNotFoundError:
    from backend.core.services.whisper_training import export_faster_whisper_ready_checkpoint


def main():
    parser = argparse.ArgumentParser(description='Export a fine-tuned Whisper/LoRA model as a faster-whisper compatible checkpoint.')
    parser.add_argument('--model_dir', required=True, help='Directory containing the fine-tuned model and PEFT adapters.')
    parser.add_argument('--output_dir', required=True, help='Directory to write the exported faster-whisper-ready model files.')
    args = parser.parse_args()

    exported_dir = export_faster_whisper_ready_checkpoint(args.model_dir, args.output_dir)
    print(f'Exported checkpoint to: {exported_dir}')


if __name__ == '__main__':
    main()
