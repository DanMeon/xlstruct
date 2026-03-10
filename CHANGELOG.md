# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-03-10

### Added

- Schema-driven Excel extraction via Pydantic models
- Two extraction modes: direct LLM extraction and code generation
- `HybridReader` — calamine (Rust) + openpyxl dual-pass reader
- `CompressedEncoder` — token-aware sheet encoding with sampling
- `ChunkSplitter` — automatic chunking for large sheets
- Code generation pipeline with self-correction (Analyzer → Parser → Transformer)
- Sandboxed script execution (`SubprocessBackend`) with blocked imports and stripped credentials
- Multi-provider LLM support via Instructor (OpenAI, Anthropic, Gemini)
- Cloud storage support via fsspec (S3, Azure Blob, GCS)
- Async-first API with `*_sync()` convenience wrappers
- Typer CLI (`xlstruct extract`)
