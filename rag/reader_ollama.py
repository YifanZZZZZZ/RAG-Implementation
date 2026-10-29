"""Replay saved reader prompts through local Ollama, preserving retrieved context."""
import argparse
import hashlib
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen


def api(endpoint, payload=None):
    request = Request('http://127.0.0.1:11434/api/' + endpoint,
                      data=None if payload is None else json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=1800) as response:
        result = json.load(response)
    if 'error' in result:
        raise RuntimeError(result['error'])
    return result


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    from .reader_prompts import parse_answer
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-run', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--model', default='mistral:7b-instruct-v0.3-q4_K_M')
    p.add_argument('--andrewid')
    p.add_argument('--tokenizer', default='mistralai/Mistral-7B-Instruct-v0.3',
                   help='Matching Hugging Face tokenizer ID or local directory; downloads tokenizer files only')
    p.add_argument('--context-window', type=int, help='Optional context override; must fit all selected prompts')
    p.add_argument('--questions', type=Path, help='Replay only these question IDs, in file order; references are ignored')
    args = p.parse_args()
    if args.output.exists():
        raise FileExistsError('Use a new output directory')
    if args.andrewid is not None and (not args.andrewid.strip() or args.andrewid == 'YOUR_ANDREWID' or any(c.isspace() for c in args.andrewid)):
        raise ValueError('Supply your actual Andrew ID without whitespace')
    source = args.input_run / 'answers.jsonl'
    rows = [json.loads(line) for line in source.read_text().splitlines() if line.strip()]
    original = json.loads((args.input_run / 'run.json').read_text())
    cfg = original['config']
    ids = [str(row['id']) for row in rows]
    if not rows or len(set(ids)) != len(ids) or set(ids) != {str(q['id']) for q in original['questions']} or 'andrewid' in ids:
        raise ValueError('Input run must have complete, unique answers matching its questions')
    if args.questions:
        from .reader import questions_from
        selected = questions_from(args.questions)
        by_id = {str(row['id']): row for row in rows}
        for question in selected:
            if question['id'] not in by_id:
                raise ValueError('Question absent from input run: ' + question['id'])
            if by_id[question['id']]['question'] != question['question']:
                raise ValueError('Question text differs from input run: ' + question['id'])
        rows = [by_id[question['id']] for question in selected]
    for row in rows:
        if not row.get('messages') or not isinstance(row.get('context'), list):
            raise ValueError('Saved messages/context are required for exact prompt replay')
    models = api('tags')['models']
    model = next((m for m in models if m['name'] == args.model), None)
    if model is None:
        raise ValueError('Model not installed; run ollama pull ' + args.model)
    metadata = api('show', {'model': args.model})
    info = metadata.get('model_info', {})
    context_limits = [int(v) for k, v in info.items() if k.endswith('.context_length')]
    if not context_limits:
        raise ValueError('Cannot determine Ollama model context limit')
    # Count the single-turn Mistral [INST] prompt with its matching tokenizer,
    # rather than treating every UTF-8 byte as a token. Keep a small BOS/template
    # reserve for HF/Ollama tokenizer implementation differences.
    from transformers import AutoTokenizer
    if not args.model.startswith('mistral:'):
        raise ValueError('This prompt-budget adapter supports Mistral single-turn chat only')
    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, trust_remote_code=False)
    prompt_counts = []
    for row in rows:
        messages = row['messages']
        if [m['role'] for m in messages] != ['system', 'user']:
            raise ValueError('Expected exactly one system message and one user message')
        rendered = '[INST] ' + messages[0]['content'] + '\n\n' + messages[1]['content'] + '[/INST]'
        prompt_counts.append(len(tokenizer.encode(rendered, add_special_tokens=True, truncation=False)))
    reserve = 64
    required = max(prompt_counts) + cfg['max_new_tokens'] + reserve
    model_limit = min(context_limits)
    if required > model_limit:
        raise ValueError(f'Actual tokenizer budget requires {required} tokens, exceeding model capacity {model_limit}; no context was removed')
    if args.context_window is not None:
        ctx = args.context_window
        if not required <= ctx <= model_limit:
            raise ValueError(f'--context-window must be between {required} and {model_limit}')
    else:
        ctx = min(model_limit, max(cfg['context_window'], ((required+1023)//1024)*1024))
    print(f'Mistral prompt tokens: max={max(prompt_counts)}, generation reserve={cfg["max_new_tokens"]}, context={ctx}; all saved evidence retained', flush=True)
    args.output.mkdir(parents=True)
    run = {'status': 'running', 'backend': 'ollama', 'model': args.model,
           'model_digest': model.get('digest'), 'input_run': str(args.input_run.resolve()),
           'input_answers_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
           'context_window': ctx, 'context_budget_policy': 'Mistral single-turn INST serialization counted with matching tokenizer plus 64-token reserve; no context removed',
           'tokenizer': args.tokenizer, 'tokenizer_class': type(tokenizer).__name__,
           'estimated_prompt_tokens_max': max(prompt_counts),
           'budget_implementation': 'mistral-tokenizer-budget-v2',
           'source_config': cfg, 'model_metadata': metadata, 'ollama_version': api('version')}
    run['questions'] = [{'id': str(row['id']), 'question': row['question']} for row in rows]
    run['question_count'] = len(rows)
    if args.questions:
        run['question_file'] = str(args.questions.resolve())
        run['question_file_sha256'] = hashlib.sha256(args.questions.read_bytes()).hexdigest()
    save(args.output / 'run.json', run)
    predictions = []
    try:
        with (args.output / 'answers.jsonl').open('w', encoding='utf-8') as out:
            for i, row in enumerate(rows, 1):
                print(f'Reading {i}/{len(rows)} | Ollama {args.model}', flush=True)
                seed = row.get('seed', cfg['seed'])
                started = time.monotonic()
                response = api('chat', {'model': args.model, 'messages': row['messages'], 'stream': False,
                    'keep_alive': '5m', 'options': {'num_ctx': ctx, 'num_predict': cfg['max_new_tokens'],
                    'temperature': cfg['temperature'], 'top_p': cfg['top_p'],
                    'top_k': cfg['sampling_top_k'], 'seed': seed}})
                if not response.get('done'):
                    raise RuntimeError('Incomplete Ollama response')
                actual = response.get('prompt_eval_count')
                if actual is not None and actual < prompt_counts[i-1] - reserve:
                    raise RuntimeError('Ollama prompt token count is unexpectedly short; inspect template/tokenizer before scoring')
                parsed = parse_answer(response['message']['content'], row['context'], cfg['mode'])
                if not parsed['answer'].strip():
                    raise RuntimeError('Empty answer')
                if response.get('done_reason') == 'length':
                    parsed['warnings'].append('generation_hit_token_limit')
                result = {'id': str(row['id']), 'question': row['question'], **parsed,
                          'messages': row['messages'], 'context': row['context'],
                          'excluded_chunk_ids': row.get('excluded_chunk_ids', []),
                          'input_tokens': response.get('prompt_eval_count'),
                          'estimated_input_tokens': prompt_counts[i-1],
                          'generation_tokens': response.get('eval_count'), 'seed': seed,
                          'finish_reason': response.get('done_reason'), 'reader_seconds': time.monotonic()-started}
                out.write(json.dumps(result, ensure_ascii=False)+'\n'); out.flush()
                predictions.append({k: result[k] for k in ('id', 'question', 'answer')})
                print('Answer: ' + parsed['answer'], flush=True)
        save(args.output / 'predictions.json', predictions)
        if args.andrewid:
            save(args.output / 'submission.json', {'andrewid': args.andrewid, **{r['id']: r['answer'] for r in predictions}})
        run['status'] = 'complete'
    except Exception as exc:
        run.update(status='failed', error=str(exc))
        raise
    finally:
        save(args.output / 'run.json', run)
    print('Saved: ' + str(args.output), flush=True)


if __name__ == '__main__':
    main()
