const PAGE_SIZE = 100

export async function fetchAllRooms(fetchPage) {
  const all = []
  while (true) {
    const { rooms } = await fetchPage(PAGE_SIZE, all.length)
    all.push(...rooms)
    if (rooms.length < PAGE_SIZE) return all
  }
}
