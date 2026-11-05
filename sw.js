const C='crc-shell-v8';
const SHELL=['./','./index.html','./styles.css','./app.js','./manifest.webmanifest'];

self.addEventListener('install',e=>{
  e.waitUntil(caches.open(C).then(c=>c.addAll(SHELL)).then(()=>self.skipWaiting()));
});

self.addEventListener('activate',e=>{
  e.waitUntil(
    caches.keys()
      .then(keys=>Promise.all(keys.filter(key=>key!==C).map(key=>caches.delete(key))))
      .then(()=>self.clients.claim())
  );
});

self.addEventListener('fetch',e=>{
  if(e.request.method!=='GET') return;

  const url=new URL(e.request.url);
  if(url.pathname.endsWith('/data/sessions.json')){
    e.respondWith(
      fetch(e.request,{cache:'no-store'})
        .then(response=>{
          if(response.ok){
            const copy=response.clone();
            caches.open(C).then(cache=>cache.put(e.request,copy));
          }
          return response;
        })
        .catch(()=>caches.match(e.request))
    );
    return;
  }

  e.respondWith(
    caches.match(e.request).then(cached=>cached||fetch(e.request).then(response=>{
      if(response.ok){
        const copy=response.clone();
        caches.open(C).then(cache=>cache.put(e.request,copy));
      }
      return response;
    }))
  );
});
