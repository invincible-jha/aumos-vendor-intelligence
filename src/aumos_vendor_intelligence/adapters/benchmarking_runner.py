"""BenchmarkingRunner adapter for cross-provider AI model benchmarking.

Provides latency, cost, quality, and throughput benchmarking across
multiple AI providers and models. Supports scheduling, result persistence,
and comparison report generation.
"""

import asyncio
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from aumos_common.observability import get_logger

logger = get_logger(__name__)

# Default benchmark configuration
DEFAULT_WARMUP_REQUESTS: int = 3
DEFAULT_BENCHMARK_REQUESTS: int = 20
DEFAULT_CONCURRENCY: int = 5
DEFAULT_TIMEOUT_SECONDS: float = 30.0

# Quality metric weights for composite scoring
QUALITY_WEIGHTS: dict[str, float] = {
    "bleu": 0.30,
    "semantic_similarity": 0.40,
    "coherence": 0.20,
    "factuality": 0.10,
}


class BenchmarkingRunner:
    """Cross-provider AI model benchmarking and comparison engine.

    Measures latency, cost-per-token, output quality (BLEU and semantic
    similarity), and throughput across multiple providers and models.
    Supports scheduled benchmark runs and generates structured comparison
    reports suitable for procurement decisions.

    All benchmark runs are tenant-scoped and persisted for historical
    trend analysis by the dashboard aggregator.
    """

    def __init__(
        self,
        result_store: dict[str, Any] | None = None,
        benchmark_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        warmup_requests: int = DEFAULT_WARMUP_REQUESTS,
        benchmark_requests: int = DEFAULT_BENCHMARK_REQUESTS,
        concurrency: int = DEFAULT_CONCURRENCY,
    ) -> None:
        """Initialise the BenchmarkingRunner.

        Args:
            result_store: Optional in-memory store for benchmark results
                (injected for testing; production implementations use the DB).
            benchmark_timeout_seconds: Per-request timeout during benchmarking.
            warmup_requests: Number of warmup requests to discard from stats.
            benchmark_requests: Number of timed requests per provider/model.
            concurrency: Maximum concurrent requests during throughput tests.
        """
        self._results: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._result_store = result_store or {}
        self._timeout = benchmark_timeout_seconds
        self._warmup_requests = warmup_requests
        self._benchmark_requests = benchmark_requests
        self._concurrency = concurrency
        self._scheduled_runs: list[dict[str, Any]] = []

    async def run_latency_benchmark(
        self,
        tenant_id: uuid.UUID,
        provider: str,
        model_name: str,
        prompt: str,
        invoke_fn: Any,
        request_count: int | None = None,
    ) -> dict[str, float]:
        """Measure latency statistics for a provider/model pair.

        Sends multiple requests with warmup discarded, computing P50/P95/P99
        latency percentiles and mean response time.

        Args:
            tenant_id: Requesting tenant UUID.
            provider: Provider identifier (e.g., "openai", "anthropic").
            model_name: Model identifier (e.g., "gpt-4o", "claude-opus-4").
            prompt: Test prompt to use for benchmarking.
            invoke_fn: Async callable accepting (provider, model_name, prompt)
                and returning a dict with "latency_ms" and "tokens" keys.
            request_count: Override default benchmark request count.

        Returns:
            Dict with p50_ms, p95_ms, p99_ms, mean_ms, min_ms, max_ms,
            error_rate, and total_requests fields.
        """
        total_requests = request_count or self._benchmark_requests
        latencies: list[float] = []
        error_count = 0

        logger.info(
            "Latency benchmark started",
            tenant_id=str(tenant_id),
            provider=provider,
            model_name=model_name,
            warmup_requests=self._warmup_requests,
            benchmark_requests=total_requests,
        )

        # Warmup phase — results discarded
        for _ in range(self._warmup_requests):
            try:
                await asyncio.wait_for(
                    invoke_fn(provider, model_name, prompt),
                    timeout=self._timeout,
                )
            except (asyncio.TimeoutError, Exception):
                pass

        # Timed benchmark phase
        for _ in range(total_requests):
            start = time.perf_counter()
            try:
                await asyncio.wait_for(
                    invoke_fn(provider, model_name, prompt),
                    timeout=self._timeout,
                )
                elapsed_ms = (time.perf_counter() - start) * 1000
                latencies.append(elapsed_ms)
            except (asyncio.TimeoutError, Exception) as exc:
                error_count += 1
                logger.warning(
                    "Benchmark request failed",
                    provider=provider,
                    model_name=model_name,
                    error=str(exc),
                )

        if not latencies:
            return {
                "p50_ms": 0.0,
                "p95_ms": 0.0,
                "p99_ms": 0.0,
                "mean_ms": 0.0,
                "min_ms": 0.0,
                "max_ms": 0.0,
                "error_rate": 1.0,
                "total_requests": total_requests,
            }

        sorted_latencies = sorted(latencies)
        n = len(sorted_latencies)

        def percentile(data: list[float], pct: float) -> float:
            index = int(pct / 100 * n)
            return data[min(index, n - 1)]

        result: dict[str, float] = {
            "p50_ms": round(percentile(sorted_latencies, 50), 2),
            "p95_ms": round(percentile(sorted_latencies, 95), 2),
            "p99_ms": round(percentile(sorted_latencies, 99), 2),
            "mean_ms": round(sum(latencies) / len(latencies), 2),
            "min_ms": round(sorted_latencies[0], 2),
            "max_ms": round(sorted_latencies[-1], 2),
            "error_rate": round(error_count / total_requests, 4),
            "total_requests": float(total_requests),
        }

        logger.info(
            "Latency benchmark completed",
            provider=provider,
            model_name=model_name,
            p50_ms=result["p50_ms"],
            p95_ms=result["p95_ms"],
            error_rate=result["error_rate"],
        )

        return result

    async def compare_cost_per_token(
        self,
        tenant_id: uuid.UUID,
        providers: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Compare cost-per-token across providers for equivalent models.

        Args:
            tenant_id: Requesting tenant UUID.
            providers: List of provider dicts, each with keys:
                - provider: str (provider name)
                - model_name: str (model identifier)
                - input_cost_per_million: float (USD per million input tokens)
                - output_cost_per_million: float (USD per million output tokens)
                - model_capability: str (capability tier: economy/standard/premium)

        Returns:
            List of comparison dicts sorted by blended_cost_per_million ascending,
            each containing provider, model_name, input_cost, output_cost,
            blended_cost, cost_rank, and capability fields.
        """
        logger.info(
            "Cost-per-token comparison started",
            tenant_id=str(tenant_id),
            provider_count=len(providers),
        )

        comparisons: list[dict[str, Any]] = []
        for provider_info in providers:
            input_cost = float(provider_info.get("input_cost_per_million", 0.0))
            output_cost = float(provider_info.get("output_cost_per_million", 0.0))
            # Blended cost assumes 1:3 input-to-output ratio typical of chat workloads
            blended_cost = round((input_cost * 1 + output_cost * 3) / 4, 4)

            comparisons.append({
                "provider": provider_info.get("provider", "unknown"),
                "model_name": provider_info.get("model_name", "unknown"),
                "input_cost_per_million_usd": input_cost,
                "output_cost_per_million_usd": output_cost,
                "blended_cost_per_million_usd": blended_cost,
                "model_capability": provider_info.get("model_capability", "standard"),
            })

        comparisons.sort(key=lambda x: x["blended_cost_per_million_usd"])
        for rank, comparison in enumerate(comparisons, start=1):
            comparison["cost_rank"] = rank

        logger.info(
            "Cost-per-token comparison completed",
            tenant_id=str(tenant_id),
            provider_count=len(comparisons),
            cheapest_provider=comparisons[0]["provider"] if comparisons else "none",
        )

        return comparisons

    async def score_output_quality(
        self,
        tenant_id: uuid.UUID,
        provider: str,
        model_name: str,
        test_cases: list[dict[str, Any]],
        invoke_fn: Any,
    ) -> dict[str, float]:
        """Score model output quality using BLEU and semantic similarity proxies.

        Args:
            tenant_id: Requesting tenant UUID.
            provider: Provider identifier.
            model_name: Model identifier.
            test_cases: List of test case dicts with "prompt" and "expected_output" keys.
            invoke_fn: Async callable returning dict with "output" key.

        Returns:
            Dict with bleu_score, semantic_similarity, coherence_score,
            factuality_score, composite_quality_score, and test_case_count fields.
        """
        logger.info(
            "Quality scoring started",
            tenant_id=str(tenant_id),
            provider=provider,
            model_name=model_name,
            test_case_count=len(test_cases),
        )

        bleu_scores: list[float] = []
        semantic_scores: list[float] = []
        coherence_scores: list[float] = []
        factuality_scores: list[float] = []

        for test_case in test_cases:
            prompt = test_case.get("prompt", "")
            expected = test_case.get("expected_output", "")

            try:
                response = await asyncio.wait_for(
                    invoke_fn(provider, model_name, prompt),
                    timeout=self._timeout,
                )
                actual_output = response.get("output", "")

                bleu = self._compute_bleu_proxy(expected, actual_output)
                semantic = self._compute_semantic_similarity_proxy(expected, actual_output)
                coherence = self._compute_coherence_score(actual_output)
                factuality = test_case.get("factuality_score", 0.8)

                bleu_scores.append(bleu)
                semantic_scores.append(semantic)
                coherence_scores.append(coherence)
                factuality_scores.append(factuality)

            except (asyncio.TimeoutError, Exception) as exc:
                logger.warning(
                    "Quality test case failed",
                    provider=provider,
                    model_name=model_name,
                    error=str(exc),
                )
                # Penalise failed test cases
                bleu_scores.append(0.0)
                semantic_scores.append(0.0)
                coherence_scores.append(0.0)
                factuality_scores.append(0.0)

        if not bleu_scores:
            return {
                "bleu_score": 0.0,
                "semantic_similarity": 0.0,
                "coherence_score": 0.0,
                "factuality_score": 0.0,
                "composite_quality_score": 0.0,
                "test_case_count": 0.0,
            }

        avg_bleu = sum(bleu_scores) / len(bleu_scores)
        avg_semantic = sum(semantic_scores) / len(semantic_scores)
        avg_coherence = sum(coherence_scores) / len(coherence_scores)
        avg_factuality = sum(factuality_scores) / len(factuality_scores)

        composite = (
            avg_bleu * QUALITY_WEIGHTS["bleu"]
            + avg_semantic * QUALITY_WEIGHTS["semantic_similarity"]
            + avg_coherence * QUALITY_WEIGHTS["coherence"]
            + avg_factuality * QUALITY_WEIGHTS["factuality"]
        )

        result: dict[str, float] = {
            "bleu_score": round(avg_bleu, 4),
            "semantic_similarity": round(avg_semantic, 4),
            "coherence_score": round(avg_coherence, 4),
            "factuality_score": round(avg_factuality, 4),
            "composite_quality_score": round(composite, 4),
            "test_case_count": float(len(test_cases)),
        }

        logger.info(
            "Quality scoring completed",
            provider=provider,
            model_name=model_name,
            composite_quality_score=result["composite_quality_score"],
        )

        return result

    async def measure_throughput(
        self,
        tenant_id: uuid.UUID,
        provider: str,
        model_name: str,
        prompt: str,
        invoke_fn: Any,
        duration_seconds: float = 10.0,
    ) -> dict[str, float]:
        """Measure requests-per-second throughput for a provider/model.

        Args:
            tenant_id: Requesting tenant UUID.
            provider: Provider identifier.
            model_name: Model identifier.
            prompt: Test prompt to use.
            invoke_fn: Async callable for model invocation.
            duration_seconds: Total duration of the throughput test.

        Returns:
            Dict with requests_per_second, successful_requests, failed_requests,
            total_tokens_per_second, and duration_seconds fields.
        """
        logger.info(
            "Throughput measurement started",
            tenant_id=str(tenant_id),
            provider=provider,
            model_name=model_name,
            duration_seconds=duration_seconds,
        )

        successful = 0
        failed = 0
        total_tokens = 0
        start_time = time.perf_counter()
        semaphore = asyncio.Semaphore(self._concurrency)

        async def bounded_invoke() -> None:
            nonlocal successful, failed, total_tokens
            async with semaphore:
                try:
                    response = await asyncio.wait_for(
                        invoke_fn(provider, model_name, prompt),
                        timeout=self._timeout,
                    )
                    successful += 1
                    total_tokens += response.get("total_tokens", 0)
                except (asyncio.TimeoutError, Exception):
                    failed += 1

        tasks: list[asyncio.Task[None]] = []
        while time.perf_counter() - start_time < duration_seconds:
            task = asyncio.create_task(bounded_invoke())
            tasks.append(task)
            await asyncio.sleep(0.01)  # Small yield to allow concurrent execution

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        elapsed = time.perf_counter() - start_time
        total_requests = successful + failed
        rps = successful / elapsed if elapsed > 0 else 0.0
        tps = total_tokens / elapsed if elapsed > 0 else 0.0

        result: dict[str, float] = {
            "requests_per_second": round(rps, 2),
            "successful_requests": float(successful),
            "failed_requests": float(failed),
            "total_tokens_per_second": round(tps, 2),
            "duration_seconds": round(elapsed, 2),
            "total_requests": float(total_requests),
            "error_rate": round(failed / total_requests, 4) if total_requests > 0 else 0.0,
        }

        logger.info(
            "Throughput measurement completed",
            provider=provider,
            model_name=model_name,
            requests_per_second=result["requests_per_second"],
        )

        return result

    def schedule_benchmark(
        self,
        tenant_id: uuid.UUID,
        benchmark_name: str,
        providers: list[str],
        models: list[str],
        cron_expression: str,
        test_prompts: list[str],
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Schedule a recurring benchmark run.

        Args:
            tenant_id: Requesting tenant UUID.
            benchmark_name: Human-readable benchmark schedule name.
            providers: List of provider identifiers to include.
            models: List of model identifiers to benchmark.
            cron_expression: Cron expression for schedule timing.
            test_prompts: List of prompts to use in benchmark runs.
            enabled: Whether the schedule is active.

        Returns:
            Dict with schedule_id, benchmark_name, cron_expression, providers,
            models, and enabled fields.
        """
        schedule_id = str(uuid.uuid4())
        schedule: dict[str, Any] = {
            "schedule_id": schedule_id,
            "tenant_id": str(tenant_id),
            "benchmark_name": benchmark_name,
            "providers": providers,
            "models": models,
            "cron_expression": cron_expression,
            "test_prompts": test_prompts,
            "enabled": enabled,
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "last_run_at": None,
            "next_run_at": None,
        }
        self._scheduled_runs.append(schedule)

        logger.info(
            "Benchmark scheduled",
            tenant_id=str(tenant_id),
            schedule_id=schedule_id,
            benchmark_name=benchmark_name,
            cron_expression=cron_expression,
            provider_count=len(providers),
        )

        return schedule

    async def persist_results(
        self,
        tenant_id: uuid.UUID,
        benchmark_name: str,
        run_id: str,
        provider: str,
        model_name: str,
        latency_stats: dict[str, float],
        cost_stats: dict[str, float],
        quality_stats: dict[str, float],
        throughput_stats: dict[str, float],
    ) -> dict[str, Any]:
        """Persist benchmark results for historical tracking.

        Args:
            tenant_id: Requesting tenant UUID.
            benchmark_name: Name of the benchmark suite.
            run_id: Unique identifier for this benchmark run.
            provider: Provider identifier.
            model_name: Model identifier.
            latency_stats: Latency benchmark results.
            cost_stats: Cost comparison results.
            quality_stats: Quality scoring results.
            throughput_stats: Throughput measurement results.

        Returns:
            Persisted result record dict.
        """
        result_record: dict[str, Any] = {
            "result_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "benchmark_name": benchmark_name,
            "run_id": run_id,
            "provider": provider,
            "model_name": model_name,
            "latency_stats": latency_stats,
            "cost_stats": cost_stats,
            "quality_stats": quality_stats,
            "throughput_stats": throughput_stats,
            "recorded_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        key = f"{tenant_id}:{benchmark_name}"
        if key not in self._result_store:
            self._result_store[key] = []
        self._result_store[key].append(result_record)

        logger.info(
            "Benchmark results persisted",
            tenant_id=str(tenant_id),
            run_id=run_id,
            provider=provider,
            model_name=model_name,
        )

        return result_record

    async def generate_comparison_report(
        self,
        tenant_id: uuid.UUID,
        run_id: str,
        benchmark_name: str,
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Generate a structured comparison report from benchmark results.

        Args:
            tenant_id: Requesting tenant UUID.
            run_id: Benchmark run identifier.
            benchmark_name: Name of the benchmark suite.
            results: List of per-provider/model benchmark result dicts.

        Returns:
            Structured comparison report dict with rankings, summary stats,
            and actionable recommendation sections.
        """
        if not results:
            return {
                "report_id": str(uuid.uuid4()),
                "tenant_id": str(tenant_id),
                "run_id": run_id,
                "benchmark_name": benchmark_name,
                "generated_at": datetime.now(tz=timezone.utc).isoformat(),
                "providers_evaluated": 0,
                "rankings": [],
                "summary": "No results available for this benchmark run.",
                "top_recommendation": None,
            }

        # Compute composite ranking score: cost-efficiency, quality, latency
        for result in results:
            latency_mean = result.get("latency_stats", {}).get("mean_ms", 9999.0)
            quality = result.get("quality_stats", {}).get("composite_quality_score", 0.0)
            cost_per_million = result.get("cost_stats", {}).get("blended_cost_per_million_usd", 999.0)
            throughput = result.get("throughput_stats", {}).get("requests_per_second", 0.0)
            error_rate = result.get("latency_stats", {}).get("error_rate", 1.0)

            # Normalise scores — higher is better in all cases after inversion
            cost_score = max(0.0, 1.0 - (cost_per_million / 100.0))  # Assumes max $100/M
            latency_score = max(0.0, 1.0 - (latency_mean / 10000.0))  # Assumes max 10s
            availability_score = 1.0 - error_rate

            composite = (
                cost_score * 0.30
                + quality * 0.35
                + latency_score * 0.20
                + availability_score * 0.10
                + min(throughput / 100.0, 1.0) * 0.05
            )
            result["composite_rank_score"] = round(composite, 4)

        results.sort(key=lambda r: r["composite_rank_score"], reverse=True)
        for rank, result in enumerate(results, start=1):
            result["overall_rank"] = rank

        top = results[0]
        top_provider = top.get("provider", "unknown")
        top_model = top.get("model_name", "unknown")

        report: dict[str, Any] = {
            "report_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "run_id": run_id,
            "benchmark_name": benchmark_name,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "providers_evaluated": len(results),
            "rankings": results,
            "summary": (
                f"Evaluated {len(results)} provider/model combinations. "
                f"Top performer: {top_provider}/{top_model} "
                f"(composite score: {top['composite_rank_score']:.3f})."
            ),
            "top_recommendation": {
                "provider": top_provider,
                "model_name": top_model,
                "composite_score": top["composite_rank_score"],
                "rationale": (
                    f"{top_provider}/{top_model} ranked highest across cost, quality, "
                    f"latency, and availability dimensions."
                ),
            },
        }

        logger.info(
            "Benchmark comparison report generated",
            tenant_id=str(tenant_id),
            run_id=run_id,
            benchmark_name=benchmark_name,
            providers_evaluated=len(results),
            top_provider=top_provider,
            top_model=top_model,
        )

        return report

    @staticmethod
    def _compute_bleu_proxy(reference: str, hypothesis: str) -> float:
        """Compute a lightweight BLEU-like n-gram overlap score.

        Uses unigram and bigram precision as a proxy for full BLEU
        to avoid heavy NLP library dependencies in the adapter layer.

        Args:
            reference: Expected model output string.
            hypothesis: Actual model output string.

        Returns:
            Score between 0.0 and 1.0.
        """
        if not reference or not hypothesis:
            return 0.0

        ref_tokens = reference.lower().split()
        hyp_tokens = hypothesis.lower().split()

        if not ref_tokens or not hyp_tokens:
            return 0.0

        # Unigram precision
        ref_set = set(ref_tokens)
        unigram_matches = sum(1 for t in hyp_tokens if t in ref_set)
        unigram_precision = unigram_matches / len(hyp_tokens)

        # Bigram precision
        ref_bigrams = set(zip(ref_tokens[:-1], ref_tokens[1:]))
        hyp_bigrams = list(zip(hyp_tokens[:-1], hyp_tokens[1:]))
        if hyp_bigrams:
            bigram_matches = sum(1 for bg in hyp_bigrams if bg in ref_bigrams)
            bigram_precision = bigram_matches / len(hyp_bigrams)
        else:
            bigram_precision = 0.0

        # Brevity penalty
        bp = min(1.0, len(hyp_tokens) / len(ref_tokens))

        bleu = bp * (unigram_precision * 0.5 + bigram_precision * 0.5)
        return round(min(bleu, 1.0), 4)

    @staticmethod
    def _compute_semantic_similarity_proxy(reference: str, hypothesis: str) -> float:
        """Estimate semantic similarity via token Jaccard overlap as proxy.

        Args:
            reference: Expected model output string.
            hypothesis: Actual model output string.

        Returns:
            Score between 0.0 and 1.0.
        """
        if not reference or not hypothesis:
            return 0.0

        ref_tokens = set(reference.lower().split())
        hyp_tokens = set(hypothesis.lower().split())

        intersection = ref_tokens & hyp_tokens
        union = ref_tokens | hyp_tokens

        if not union:
            return 0.0

        return round(len(intersection) / len(union), 4)

    @staticmethod
    def _compute_coherence_score(text: str) -> float:
        """Estimate text coherence via structural heuristics.

        Args:
            text: Model output text to score.

        Returns:
            Score between 0.0 and 1.0.
        """
        if not text:
            return 0.0

        words = text.split()
        if len(words) < 5:
            return 0.3

        sentences = [s.strip() for s in text.split(".") if s.strip()]
        if not sentences:
            return 0.5

        # Heuristics: reasonable length, starts with capital, has punctuation
        avg_sentence_length = len(words) / len(sentences)
        length_score = min(avg_sentence_length / 15.0, 1.0)
        starts_capital = float(text[0].isupper()) if text else 0.0
        has_punctuation = float(any(c in text for c in ".!?,;:"))

        coherence = (length_score * 0.5 + starts_capital * 0.3 + has_punctuation * 0.2)
        return round(min(coherence, 1.0), 4)
