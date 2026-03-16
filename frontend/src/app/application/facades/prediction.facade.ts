import { Injectable, inject, signal } from '@angular/core';
import { PredictionApiClient } from '../../infrastructure/api-clients/prediction-api.client';
import { NotificationService } from '../../shared/services/notification.service';
import {
  Prediction,
  PredictionRequest,
  PredictionList,
  BatchPredictionRequest,
} from '../../domain/models';

@Injectable({ providedIn: 'root' })
export class PredictionFacade {
  private readonly api = inject(PredictionApiClient);
  private readonly notifications = inject(NotificationService);

  readonly predictions = signal<Prediction[]>([]);
  readonly batchResults = signal<Prediction[]>([]);
  readonly loading = signal(false);
  readonly computing = signal(false);

  loadByProduct(productId: number, horizonDays?: number): void {
    this.loading.set(true);
    this.api.getByProduct(productId, horizonDays).subscribe({
      next: (data) => {
        this.predictions.set(data.predictions);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  computePrediction(request: PredictionRequest): void {
    this.computing.set(true);
    this.api.create(request).subscribe({
      next: (prediction) => {
        this.predictions.update((list) => [prediction, ...list]);
        this.computing.set(false);
        this.notifications.success('Prediccion calculada');
      },
      error: () => this.computing.set(false),
    });
  }

  computeBatch(request: BatchPredictionRequest): void {
    this.computing.set(true);
    this.api.createBatch(request).subscribe({
      next: (data) => {
        this.batchResults.set(data);
        this.computing.set(false);
        this.notifications.success(`${data.length} predicciones calculadas`);
      },
      error: () => this.computing.set(false),
    });
  }
}
