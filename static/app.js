document.querySelectorAll('input[type=file]').forEach(inp=>inp.addEventListener('change',updateFiles));
function updateFiles(){let n=0;document.querySelectorAll('input[type=file]').forEach(i=>n+=i.files.length);const e=document.getElementById('fileSummary');if(e)e.textContent=n?`${n} dosya seçildi. Formu gönderdiğinizde fotoğraflar teklifinizle birlikte iletilecek.`:'Henüz dosya seçilmedi.'}
