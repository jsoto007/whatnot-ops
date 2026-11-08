import { chatCsv, csvCell, csvTime, parseChatCsv, parseCsv } from '../src/shared/csv'

describe('chat CSV', () => {
  it('quotes every cell, doubles quotes, and defuses cells Excel would run as formulas', () => {
    expect(csvCell('plain')).toBe('"plain"')
    expect(csvCell('say "hi"')).toBe('"say ""hi"""')
    expect(csvCell('=HYPERLINK("http://evil")')).toBe('"\'=HYPERLINK(""http://evil"")"')
    expect(csvCell('+1')).toBe('"\'+1"')
    expect(csvCell('-5 please')).toBe('"\'-5 please"')
    expect(csvCell('@bob you won')).toBe('"\'@bob you won"')
    expect(csvCell('\tcmd')).toBe('"\'\tcmd"')
    expect(csvCell('a-b')).toBe('"a-b"') // only a leading character is a risk
  })

  it('writes a BOM, a header and CRLF lines that Excel opens as UTF-8', () => {
    const out = chatCsv([
      { time: '2026-11-07 20:01:05', username: 'kat.c', message: 'I\'ll take 3 🎉', flagged: true },
      { time: '2026-11-07 20:01:09', username: 'host_shop', message: 'sold to kat.c' },
    ])
    expect(out.startsWith('\uFEFF"Time","Username","Message","Flagged"\r\n')).toBe(true)
    expect(out).toContain('"2026-11-07 20:01:05","kat.c","I\'ll take 3 🎉","yes"\r\n')
    expect(out.endsWith('"2026-11-07 20:01:09","host_shop","sold to kat.c",""\r\n')).toBe(true)
  })

  it('formats the time in the local zone as Excel reads it', () => {
    const d = new Date(2026, 10, 7, 20, 1, 5)
    expect(csvTime(d.toISOString())).toBe('2026-11-07 20:01:05')
    expect(csvTime('garbage')).toBe('')
  })

  it('parses quoted fields, doubled quotes and line breaks inside quotes', () => {
    expect(parseCsv('a,b\r\n"c,1","d ""e"""\n"two\nlines",f\n')).toEqual([
      ['a', 'b'],
      ['c,1', 'd "e"'],
      ['two\nlines', 'f'],
    ])
  })

  it('reads back what it wrote, apostrophes removed, and refuses other CSVs', () => {
    const lines = [
      { time: '2026-11-07 20:01:05', username: 'kat.c', message: '=SUM(1), "quoted"\nnext line', flagged: true },
      { time: '2026-11-07 20:01:09', username: '@mod', message: 'joined', flagged: false },
    ]
    expect(parseChatCsv(chatCsv(lines))).toEqual(lines)
    expect(parseChatCsv('Item,Current Stock\nWater Gun,1\n')).toBeNull()
    expect(parseChatCsv('')).toBeNull()
  })
})
