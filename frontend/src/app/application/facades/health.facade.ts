import { Injectable, inject, signal, OnDestroy } from '@angular/core';
import { HealthApiClient } from '../../infrastructure/api-clients/health-api.client';
import { HealthStatus, PipelineHealth } from '../../domain/models';

const HEALTH_POLL_MS = 60_000;

@Injectable({ providedIn: 'root' })
export class HealthFacade implements OnDestroy {
  private readonly api = inject(HealthApiClient);
  private pollTimer: ReturnType<typeof setInterval> | null = null;

  readonly health = signal<HealthStatus | null>(null);
  readonly pipeline = signal<PipelineHealth | null>(null);
  readonly loading = signal(false);

  loadHealth(): void {
    this.api.getHealth().subscribe({
      next: (data) => this.health.set(data),
    });
  }

  loadPipeline(): void {
    this.loading.set(true);
    this.api.getPipelineHealth().subscribe({
      next: (data) => {
        this.pipeline.set(data);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  startPolling(): void {
    this.stopPolling();
    this.loadHealth();
    this.loadPipeline();
    this.pollTimer = setInterval(() => {
      this.loadHealth();
      this.loadPipeline();
    }, HEALTH_POLL_MS);
  }

  stopPolling(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  ngOnDestroy(): void {
    this.stopPolling();
  }
}
