import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { SkuPicker } from './SkuPicker'

const SKUS = [
  { id: 1, code: 'RED', name: '', productName: 'Red bundle', available: 3, aliases: [] },
  { id: 2, code: 'HOLO', name: 'Charizard', productName: 'Holo slab', available: 0, aliases: [] },
  { id: 3, code: 'RED-XL', name: 'XL', productName: 'Red bundle', available: 1, aliases: [{ alias: 'mystery box' }] },
]

function Harness({ onChange }: { onChange: (id: string) => void }) {
  const [value, setValue] = useState('')
  return (
    <SkuPicker
      skus={SKUS}
      value={value}
      onChange={(id) => {
        setValue(id)
        onChange(id)
      }}
      label="SKU for review 7"
    />
  )
}

test('typing lists the matching SKUs with their stock, and a click picks one', () => {
  const onChange = vi.fn()
  render(<Harness onChange={onChange} />)
  const box = screen.getByRole('combobox', { name: 'SKU for review 7' })
  expect(screen.queryByRole('listbox')).toBeNull()
  fireEvent.focus(box)
  expect(screen.getAllByRole('option')).toHaveLength(3)
  fireEvent.change(box, { target: { value: 'mystery' } })
  const options = screen.getAllByRole('option')
  expect(options).toHaveLength(1)
  expect(options[0]).toHaveTextContent('Red bundle XL')
  expect(options[0]).toHaveTextContent('RED-XL · 1 available')
  fireEvent.click(options[0])
  expect(onChange).toHaveBeenLastCalledWith('3')
  expect(box).toHaveValue('Red bundle · XL (RED-XL)')
  expect(screen.queryByRole('listbox')).toBeNull()
})

test('arrow keys and Enter pick without the mouse; typing again or × drops the pick; Escape closes', () => {
  const onChange = vi.fn()
  render(<Harness onChange={onChange} />)
  const box = screen.getByRole('combobox', { name: 'SKU for review 7' })
  fireEvent.focus(box)
  fireEvent.change(box, { target: { value: 'red' } })
  fireEvent.keyDown(box, { key: 'ArrowDown' })
  expect(box).toHaveAttribute('aria-activedescendant', screen.getAllByRole('option')[1].id)
  fireEvent.keyDown(box, { key: 'Enter' })
  expect(onChange).toHaveBeenLastCalledWith('3')
  expect(box).toHaveValue('Red bundle · XL (RED-XL)')

  fireEvent.change(box, { target: { value: 'holo' } })
  expect(onChange).toHaveBeenLastCalledWith('')
  expect(screen.getByRole('option')).toHaveTextContent('Holo slab Charizard')
  fireEvent.keyDown(box, { key: 'Enter' })
  expect(onChange).toHaveBeenLastCalledWith('2')

  fireEvent.click(screen.getByRole('button', { name: 'Clear SKU' }))
  expect(onChange).toHaveBeenLastCalledWith('')
  expect(box).toHaveValue('')
  fireEvent.focus(box)
  expect(screen.getByRole('listbox')).toBeInTheDocument()
  fireEvent.keyDown(box, { key: 'Escape' })
  expect(screen.queryByRole('listbox')).toBeNull()
})

test('a long catalogue shows the first 12 and says how many more there are; no match says so', () => {
  const many = Array.from({ length: 20 }, (_, i) => ({ id: i + 1, code: `C${i + 1}`, name: '', productName: `Item ${String(i + 1).padStart(2, '0')}`, available: 1, aliases: [] }))
  render(<SkuPicker skus={many} value="" onChange={() => {}} label="SKU" />)
  const box = screen.getByRole('combobox', { name: 'SKU' })
  fireEvent.focus(box)
  expect(screen.getAllByRole('option')).toHaveLength(12)
  expect(screen.getByText('8 more · keep typing to narrow it down')).toBeInTheDocument()
  fireEvent.change(box, { target: { value: 'zzz' } })
  expect(screen.queryAllByRole('option')).toHaveLength(0)
  expect(screen.getByText('Nothing matches “zzz”.')).toBeInTheDocument()
})
