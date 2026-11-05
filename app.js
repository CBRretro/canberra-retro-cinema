const body=document.querySelector('#screenings-body');
const search=document.querySelector('#search');
const count=document.querySelector('#result-count');
const updated=document.querySelector('#updated');
const cinemaFilter=document.querySelector('#cinema-filter');
let sessions=[];

const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
function dateLabel(value){
  const date=new Date(value);
  if(Number.isNaN(date.getTime())) return 'Date to be confirmed';
  return date.toLocaleDateString('en-AU',{weekday:'short',day:'numeric',month:'short',year:'numeric'});
}
function render(){
  const query=search.value.trim().toLowerCase();
  const rows=sessions
    .filter(item=>(cinemaFilter.value==='all'||item.cinema===cinemaFilter.value))
    .filter(item=>!query||[item.title,item.year,item.cinema].join(' ').toLowerCase().includes(query))
    .sort((a,b)=>new Date(a.start)-new Date(b.start)||a.title.localeCompare(b.title));
  count.textContent=rows.length?rows.length+' SCREENINGS':'0 SCREENINGS';
  if(!rows.length){
    body.innerHTML='<tr><td colspan="4" class="empty-cell">'+(sessions.length?'No screenings match your search.':'No screenings are currently available.')+'</td></tr>';
    return;
  }
  body.innerHTML=rows.map(item=>'<tr><td>'+esc(item.title)+'</td><td>'+esc(item.year||'—')+'</td><td>'+esc(item.cinema)+'</td><td>'+esc(dateLabel(item.start))+'</td></tr>').join('');
}
search.addEventListener('input',render);
cinemaFilter.addEventListener('change',render);
fetch('./data/sessions.json',{cache:'no-store'})
  .then(response=>{if(!response.ok)throw new Error('Could not load listings');return response.json();})
  .then(data=>{
    sessions=Array.isArray(data.sessions)?data.sessions:[];
    if(data.updated_at)updated.textContent='Last checked: '+new Date(data.updated_at).toLocaleString('en-AU')+'. Screening dates may change; confirm with the cinema.';
    render();
  })
  .catch(()=>{body.innerHTML='<tr><td colspan="4" class="empty-cell">The listings could not be loaded. Please try refreshing.</td></tr>';});
