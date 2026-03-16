import { HttpInterceptorFn, HttpErrorResponse } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, retry, timer, throwError } from 'rxjs';
import { NotificationService } from '../../shared/services/notification.service';

const MAX_RETRIES = 2;
const RETRY_DELAY_MS = 1000;

export const errorInterceptor: HttpInterceptorFn = (req, next) => {
  const notifications = inject(NotificationService);

  return next(req).pipe(
    retry({
      count: MAX_RETRIES,
      delay: (error, retryCount) => {
        // Only retry on 5xx or network errors
        if (error instanceof HttpErrorResponse && error.status >= 500) {
          return timer(RETRY_DELAY_MS * retryCount);
        }
        if (error.status === 0) {
          return timer(RETRY_DELAY_MS * retryCount);
        }
        return throwError(() => error);
      },
    }),
    catchError((error: HttpErrorResponse) => {
      // 404 is expected for many endpoints (no config yet, etc.) — let callers handle it
      if (error.status === 404) {
        return throwError(() => error);
      }

      let message = 'Error inesperado';

      if (error.status === 0) {
        message = 'Sin conexion al servidor';
      } else if (error.status === 422) {
        message = 'Datos invalidos';
      } else if (error.status === 429) {
        message = 'Demasiadas solicitudes, intenta mas tarde';
      } else if (error.status >= 500) {
        message = 'Error del servidor';
      }

      notifications.error(message);
      return throwError(() => error);
    }),
  );
};
