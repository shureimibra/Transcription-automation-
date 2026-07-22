import argparse
import os
import sys

tools_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(tools_dir, '..'))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

try:
    from core.services.whisper_training import train_whisper_lora
except ModuleNotFoundError:
    from backend.core.services.whisper_training import train_whisper_lora


def main():
    parser = argparse.ArgumentParser(description='Train a Whisper LoRA model from audio transcription data.')
    parser.add_argument('--train_file', required=True, help='Path to the training dataset file (CSV/JSON/JSONL).')
    parser.add_argument('--output_dir', required=True, help='Directory where the fine-tuned model and processor will be saved.')
    parser.add_argument('--valid_file', default=None, help='Optional validation dataset file.')
    parser.add_argument('--model_name_or_path', default='openai/whisper-small', help='Pretrained Whisper model to fine-tune.')
    parser.add_argument('--language', default=None, help='Language name for the processor, e.g. oromo.')
    parser.add_argument('--epochs', type=int, default=3, help='Number of training epochs.')
    parser.add_argument('--train_batch_size', type=int, default=4, help='Per-device training batch size.')
    parser.add_argument('--eval_batch_size', type=int, default=4, help='Per-device evaluation batch size.')
    parser.add_argument('--learning_rate', type=float, default=1e-4, help='Learning rate for optimizer.')
    parser.add_argument('--gradient_accumulation_steps', type=int, default=2, help='Gradient accumulation steps.')
    parser.add_argument('--save_steps', type=int, default=500, help='Save checkpoint every N steps.')
    parser.add_argument('--logging_steps', type=int, default=50, help='Log progress every N steps.')
    parser.add_argument('--max_steps', type=int, default=None, help='Optional maximum number of training steps.')
    parser.add_argument('--fp16', action='store_true', help='Enable mixed precision training if supported.')
    args = parser.parse_args()

    train_whisper_lora(
        train_file=args.train_file,
        valid_file=args.valid_file,
        output_dir=args.output_dir,
        model_name_or_path=args.model_name_or_path,
        language=args.language,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.train_batch_size,
        per_device_eval_batch_size=args.eval_batch_size,
        learning_rate=args.learning_rate,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        save_steps=args.save_steps,
        logging_steps=args.logging_steps,
        max_steps=args.max_steps,
        fp16=args.fp16,
    )


if __name__ == '__main__':
    main()
