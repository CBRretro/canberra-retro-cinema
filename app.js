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
function dateKey(value){
  const date=new Date(value);
  if(Number.isNaN(date.getTime())) return String(value??'');
  return date.getFullYear()+'-'+String(date.getMonth()+1).padStart(2,'0')+'-'+String(date.getDate()).padStart(2,'0');
}
function groupedListings(){
  const groups=new Map();
  sessions.forEach(item=>{
    // A film at each cinema is its own listing, even when the film ID is shared.
    const filmKey=item.film_id||[item.title,item.year||''].join('|').toLowerCase();
    const key=JSON.stringify([filmKey,item.cinema||'']);
    if(!groups.has(key)){
      groups.set(key,{...item,screenings:[]});
    }
    const group=groups.get(key);
    group.screenings.push(item.start);
  });
  return [...groups.values()].map(group=>{
    const seen=new Set();
    group.screenings=group.screenings
      .filter(value=>{
        const key=dateKey(value);
        if(seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .sort((a,b)=>new Date(a)-new Date(b));
    group.firstScreening=group.screenings[0]||group.start;
    return group;
  });
}
function render(){
  const query=search.value.trim().toLowerCase();
  const rows=groupedListings()
    .filter(item=>(cinemaFilter.value==='all'||item.cinema===cinemaFilter.value))
    .filter(item=>!query||[item.title,item.year,item.cinema].join(' ').toLowerCase().includes(query))
    .sort((a,b)=>new Date(a.firstScreening)-new Date(b.firstScreening)||a.title.localeCompare(b.title));
  const screeningTotal=rows.reduce((total,item)=>total+item.screenings.length,0);
  count.textContent=rows.length
    ?rows.length+' FILM LISTINGS · '+screeningTotal+' SCREENINGS'
    :'0 FILM LISTINGS';
  if(!rows.length){
    body.innerHTML='<tr><td colspan="4" class="empty-cell">'+(sessions.length?'No screenings match your search.':'No screenings are currently available.')+'</td></tr>';
    return;
  }
  body.innerHTML=rows.map(item=>{
    const dates=item.screenings.map(value=>'<div class="screening-date">'+esc(dateLabel(value))+'</div>').join('');
    return '<tr><td>'+esc(item.title)+'</td><td>'+esc(item.year||'—')+'</td><td class="screening-dates">'+dates+'</td><td>'+esc(item.cinema)+'</td></tr>';
  }).join('');
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
