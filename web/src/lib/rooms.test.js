import { describe, expect, it, vi } from 'vitest'
import { fetchAllRooms } from './rooms.js'

const server = (count) =>
  vi.fn(async (limit, offset) => ({
    rooms: Array.from({ length: count }, (_, id) => ({ id })).slice(offset, offset + limit),
  }))

describe('fetchAllRooms', () => {
  it('collects every page', async () => {
    const fetchPage = server(250)
    expect(await fetchAllRooms(fetchPage)).toHaveLength(250)
    expect(fetchPage).toHaveBeenCalledTimes(3)
  })

  it('stops after an exact multiple of the page size', async () => {
    const fetchPage = server(200)
    expect(await fetchAllRooms(fetchPage)).toHaveLength(200)
    expect(fetchPage).toHaveBeenCalledTimes(3)
  })

  it('handles no rooms', async () => {
    expect(await fetchAllRooms(server(0))).toEqual([])
  })
})
