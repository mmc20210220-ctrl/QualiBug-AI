// @vitest-environment jsdom
import { useState } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { Analyze } from '../Analyze';

vi.mock('../../components/useToast', () => ({ useToast: () => ({ show: vi.fn() }) }));
vi.mock('../RequirementIntelligence', () => ({ RequirementIntelligence: () => {
  const [query, setQuery] = useState('');
  return <input aria-label="需求搜索" value={query} onChange={(event) => setQuery(event.target.value)} />;
} }));
vi.mock('../TestIntelligence', () => ({ TestIntelligence: ({ view }: { view: string }) => {
  const [page, setPage] = useState(1);
  return <button onClick={() => setPage(page + 1)}>{view} 第 {page} 页</button>;
} }));
afterEach(cleanup);

it('retains each visited workspace without mounting unopened workspaces', () => {
  render(<MemoryRouter initialEntries={['/analyze?project=p&task=t']}><Analyze /></MemoryRouter>);
  expect(screen.queryByText('test-targets 第 1 页')).toBeNull();
  fireEvent.change(screen.getByRole('textbox', { name: '需求搜索' }), { target: { value: '风险' } });
  fireEvent.click(screen.getByRole('button', { name: /^测试设计/ }));
  expect(screen.queryByRole('textbox', { name: '需求搜索' })).toBeNull();
  fireEvent.click(screen.getByText('test-targets 第 1 页'));
  fireEvent.click(screen.getByRole('button', { name: /^测试数据/ }));
  expect(screen.getByText('test-data 第 1 页')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: /^测试设计/ }));
  expect(screen.getByRole('button', { name: 'test-targets 第 2 页' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: /^需求审查/ }));
  expect((screen.getByRole('textbox', { name: '需求搜索' }) as HTMLInputElement).value).toBe('风险');
});

