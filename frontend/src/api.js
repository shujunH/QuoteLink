// 通过同源代理访问 Django，所有写请求都携带 CSRF 令牌。
export async function api(path, data) {
  const token = document.cookie.split('; ').find(pair => pair.startsWith('csrftoken='))?.slice(10)
  const response = await fetch('/api/' + path, data === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token ?? '' },
    body: JSON.stringify(data)
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.error || '请求失败，请刷新页面或检查后端服务。')
  return payload
}
