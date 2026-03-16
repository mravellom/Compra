import { Pipe, PipeTransform } from '@angular/core';

@Pipe({ name: 'percentFormat', standalone: true })
export class PercentFormatPipe implements PipeTransform {
  transform(value: number | null | undefined, decimals = 1): string {
    if (value == null) return '—';
    return `${(value * 100).toFixed(decimals)}%`;
  }
}
