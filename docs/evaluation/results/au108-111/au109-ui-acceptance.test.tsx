import { readFileSync } from 'node:fs';
import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { TerrainCard } from '@/components/TerrainCard';

it('141801_4.0701.23/8: rzeczywisty snapshot daje jeden zakres UI 118,269–118,959 m i deniwelację 0,69 m', () => {
  const terrain = JSON.parse(readFileSync('/acceptance/terrain.json', 'utf8'));
  render(<TerrainCard terrain={terrain} />);
  expect(screen.getByTestId('terrain-min')).toHaveTextContent('118,269 m');
  expect(screen.getByTestId('terrain-max')).toHaveTextContent('118,959 m');
  expect(screen.getByTestId('terrain-height-difference')).toHaveTextContent('0,69 m');
  expect(screen.getAllByText('Najniższa wysokość (Hmin)')).toHaveLength(1);
  expect(screen.getAllByText('Najwyższa wysokość (Hmax)')).toHaveLength(1);
  expect(screen.queryByText('117,9 m', {exact: true})).not.toBeInTheDocument();
  expect(screen.queryByText('118,5 m', {exact: true})).not.toBeInTheDocument();
  expect(screen.getByText(/Wysokości nadrzędne: raster WCS 1 m/)).toBeInTheDocument();
  expect(screen.getByTestId('terrain-source')).toHaveTextContent('NMT_WCS');
});
