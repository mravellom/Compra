import { Injectable, inject, signal } from '@angular/core';
import { OrchestratorApiClient } from '../../infrastructure/api-clients/orchestrator-api.client';
import { NotificationService } from '../../shared/services/notification.service';
import {
  OpportunityInput,
  Decision,
  BatchOrchestrationRequest,
  Pipeline,
  DecisionStats,
} from '../../domain/models';

@Injectable({ providedIn: 'root' })
export class OrchestratorFacade {
  private readonly api = inject(OrchestratorApiClient);
  private readonly notifications = inject(NotificationService);

  readonly decisions = signal<Record<string, unknown>[]>([]);
  readonly batchDecisions = signal<Decision[]>([]);
  readonly selectedPipeline = signal<Pipeline | null>(null);
  readonly stats = signal<DecisionStats | null>(null);
  readonly loading = signal(false);
  readonly processing = signal(false);

  processOpportunity(input: OpportunityInput): void {
    this.processing.set(true);
    this.api.process(input).subscribe({
      next: (decision) => {
        this.batchDecisions.update((list) => [decision, ...list]);
        this.processing.set(false);
        this.notifications.success(`Decision: ${decision.decision} (${decision.signal_strength})`);
      },
      error: () => this.processing.set(false),
    });
  }

  processBatch(request: BatchOrchestrationRequest): void {
    this.processing.set(true);
    this.api.processBatch(request).subscribe({
      next: (data) => {
        this.batchDecisions.set(data);
        this.processing.set(false);
        this.notifications.success(`${data.length} decisiones procesadas`);
      },
      error: () => this.processing.set(false),
    });
  }

  loadPipeline(pipelineId: string): void {
    this.api.getPipeline(pipelineId).subscribe({
      next: (data) => this.selectedPipeline.set(data),
    });
  }

  loadDecisions(limit = 20): void {
    this.loading.set(true);
    this.api.getDecisions(limit).subscribe({
      next: (data) => {
        this.decisions.set(data);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  loadStats(): void {
    this.api.getStats().subscribe({
      next: (data) => this.stats.set(data),
    });
  }
}
