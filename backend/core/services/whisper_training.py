import logging
import os
from typing import Dict, Optional

from datasets import Audio, DatasetDict, load_dataset
import torch
from peft import LoraConfig, TaskType, get_peft_model
from transformers import (
    DataCollatorSpeechSeq2SeqWithPadding,
    Seq2SeqTrainer,
    Seq2SeqTrainingArguments,
    WhisperForConditionalGeneration,
    WhisperProcessor,
)

logger = logging.getLogger("WhisperTraining")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

LANGUAGE_MAP = {
    "english": "English",
    "french": "French",
    "spanish": "Spanish",
    "german": "German",
    "italian": "Italian",
    "portuguese": "Portuguese",
    "russian": "Russian",
    "japanese": "Japanese",
    "chinese": "Chinese",
    "mandarin": "Chinese",
    "arabic": "Arabic",
    "korean": "Korean",
    "hindi": "Hindi",
    "oromo": "Oromo",
}


def _resolve_language_token(language: Optional[str]) -> Optional[str]:
    if not language:
        return None
    normalized = language.strip().lower()
    return LANGUAGE_MAP.get(normalized, language.strip())


def _get_dataset_format(file_path: str) -> str:
    extension = os.path.splitext(file_path)[1].lower().lstrip('.')
    if extension in ('csv', 'json', 'jsonl'):
        return extension
    raise ValueError(f"Unsupported dataset file extension: {extension}")


def load_audio_dataset(
    train_file: str,
    valid_file: Optional[str] = None,
    audio_column: str = "audio",
    text_column: str = "text",
    sampling_rate: int = 16000,
) -> DatasetDict:
    dataset_files = {"train": train_file}
    if valid_file:
        dataset_files["validation"] = valid_file

    dataset_format = _get_dataset_format(train_file)
    dataset = load_dataset(dataset_format, data_files=dataset_files)
    dataset = dataset.cast_column(audio_column, Audio(sampling_rate=sampling_rate))
    return dataset


def prepare_batch(
    batch: Dict,
    processor: WhisperProcessor,
    audio_column: str,
    text_column: str,
) -> Dict:
    audio = batch[audio_column]
    if audio is None or audio.get("array") is None:
        raise ValueError(f"Audio content missing for row: {batch}")

    inputs = processor.feature_extractor(
        audio["array"], sampling_rate=audio["sampling_rate"]
    )
    batch["input_features"] = inputs.input_features[0]

    with processor.as_target_processor():
        labels = processor(batch[text_column]).input_ids
    batch["labels"] = labels
    return batch


def preprocess_dataset(
    raw_dataset: DatasetDict,
    processor: WhisperProcessor,
    audio_column: str = "audio",
    text_column: str = "text",
) -> DatasetDict:
    def _map_fn(batch):
        return prepare_batch(batch, processor, audio_column, text_column)

    remove_columns = [audio_column, text_column]
    prepared = raw_dataset.map(
        _map_fn,
        remove_columns=remove_columns,
        num_proc=1,
        batched=False,
    )
    return prepared


def create_peft_model(model: WhisperForConditionalGeneration, r: int = 16, alpha: int = 32, dropout: float = 0.1) -> WhisperForConditionalGeneration:
    config = LoraConfig(
        task_type=TaskType.SEQ_2_SEQ_LM,
        inference_mode=False,
        r=r,
        lora_alpha=alpha,
        lora_dropout=dropout,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
    )
    return get_peft_model(model, config)


def train_whisper_lora(
    train_file: str,
    output_dir: str,
    valid_file: Optional[str] = None,
    model_name_or_path: str = "openai/whisper-small",
    audio_column: str = "audio",
    text_column: str = "text",
    language: Optional[str] = None,
    num_train_epochs: int = 3,
    per_device_train_batch_size: int = 4,
    per_device_eval_batch_size: int = 4,
    learning_rate: float = 1e-4,
    gradient_accumulation_steps: int = 2,
    save_steps: int = 500,
    logging_steps: int = 50,
    max_steps: Optional[int] = None,
    fp16: bool = False,
) -> WhisperForConditionalGeneration:
    os.makedirs(output_dir, exist_ok=True)
    dataset = load_audio_dataset(train_file, valid_file, audio_column, text_column)
    language_token = _resolve_language_token(language)

    logger.info(f"Loading processor and model from {model_name_or_path}")
    processor = WhisperProcessor.from_pretrained(
        model_name_or_path,
        language=language_token if language_token else "English",
        task="transcribe",
    )
    model = WhisperForConditionalGeneration.from_pretrained(model_name_or_path)

    model = create_peft_model(model)

    logger.info("Preparing dataset...")
    processed_dataset = preprocess_dataset(dataset, processor, audio_column, text_column)

    data_collator = DataCollatorSpeechSeq2SeqWithPadding(
        processor=processor,
        padding=True,
        label_pad_token_id=-100,
    )

    training_args = Seq2SeqTrainingArguments(
        output_dir=output_dir,
        per_device_train_batch_size=per_device_train_batch_size,
        per_device_eval_batch_size=per_device_eval_batch_size,
        learning_rate=learning_rate,
        num_train_epochs=num_train_epochs,
        gradient_accumulation_steps=gradient_accumulation_steps,
        evaluation_strategy="steps" if valid_file else "no",
        save_strategy="steps",
        save_steps=save_steps,
        logging_steps=logging_steps,
        fp16=fp16,
        load_best_model_at_end=bool(valid_file),
        predict_with_generate=True,
        generation_max_length=256,
        remove_unused_columns=False,
        report_to="none",
    )

    trainer = Seq2SeqTrainer(
        model=model,
        args=training_args,
        train_dataset=processed_dataset["train"],
        eval_dataset=processed_dataset["validation"] if valid_file else None,
        tokenizer=processor,
        data_collator=data_collator,
    )

    logger.info("Starting LoRA fine-tuning...")
    trainer.train(max_steps=max_steps if max_steps is not None else -1)

    logger.info("Saving fine-tuned model and processor...")
    model.save_pretrained(output_dir)
    processor.save_pretrained(output_dir)

    return model


def export_faster_whisper_ready_checkpoint(model_dir: str, output_dir: str) -> str:
    if not os.path.isdir(model_dir):
        raise ValueError(f"Model directory does not exist: {model_dir}")

    os.makedirs(output_dir, exist_ok=True)
    logger.info(f"Exporting model artifacts from {model_dir} to {output_dir}")

    try:
        from peft import PeftModel
    except ImportError:
        PeftModel = None

    try:
        model = WhisperForConditionalGeneration.from_pretrained(model_dir)
    except Exception as e:
        raise RuntimeError(f"Failed to load model from {model_dir}: {e}") from e

    if PeftModel is not None and isinstance(model, PeftModel):
        logger.info("Merging LoRA adapters into the base Whisper model for faster-whisper compatibility.")
        try:
            model = model.merge_and_unload()
        except Exception as e:
            raise RuntimeError(f"Failed to merge LoRA adapters: {e}") from e

    model.save_pretrained(output_dir)

    # Copy processor and tokenizer artifacts if present in the source model directory
    for filename in os.listdir(model_dir):
        if filename.startswith("config") or filename.startswith("tokenizer") or filename.endswith(".json") or filename.endswith(".txt") or filename.endswith(".model"):
            source = os.path.join(model_dir, filename)
            dest = os.path.join(output_dir, filename)
            if os.path.isfile(source):
                with open(source, "rb") as fsrc, open(dest, "wb") as fdst:
                    fdst.write(fsrc.read())

    logger.info("Checkpoint exported. The output directory can be used with faster-whisper if it supports the Hugging Face Whisper format.")
    return output_dir
