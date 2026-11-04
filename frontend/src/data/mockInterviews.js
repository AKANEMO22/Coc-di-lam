export const questionBank = [
  'Could you introduce yourself and describe your professional background?',
  'What motivated you to apply for this position?',
  'Tell me about a difficult problem you solved and how you approached it.',
  'How do you prioritize tasks when working under a tight deadline?',
  'Describe a time you received constructive feedback. What did you do?',
  'What are your goals for the next two years?'
];
export const starterResults = [
 {id:'demo-101',role:'Software Engineer',company:'TechCorp',date:'2026-10-19T09:30:00',duration:45,score:82,status:'Completed',skills:[85,80,78,85],transcript:[{speaker:'AI Interviewer',text:'Tell me about a technical challenge you solved.'},{speaker:'You',text:'I improved an application by optimizing slow database queries.'}],feedback:'You showed strong technical fundamentals and clear communication. Try giving more measurable examples in future answers.'},
 {id:'demo-102',role:'Product Manager',company:'InnovateTech',date:'2026-10-17T14:00:00',duration:40,score:76,status:'Completed',skills:[72,82,77,73],transcript:[],feedback:'Good stakeholder awareness. Add more structure when prioritizing trade-offs.'},
 {id:'demo-103',role:'Data Analyst',company:'DataMind',date:'2026-10-15T11:00:00',duration:35,score:88,status:'Completed',skills:[91,82,90,89],transcript:[],feedback:'Strong analytical thinking and a confident, structured explanation.'},
 {id:'demo-104',role:'UI/UX Designer',company:'Creative Studio',date:'2026-10-12T16:00:00',duration:38,score:70,status:'Completed',skills:[72,74,66,68],transcript:[],feedback:'Good creative instincts. Explain the research behind your design decisions.'},
 {id:'demo-105',role:'Marketing Specialist',company:'Growth Co.',date:'2026-10-10T09:00:00',duration:30,score:80,status:'Completed',skills:[77,85,75,83],transcript:[],feedback:'Clear communication and good campaign awareness.'},
 {id:'demo-106',role:'System Administrator',company:'CloudNet',date:'2026-10-08T10:00:00',duration:42,score:68,status:'Completed',skills:[73,65,69,65],transcript:[],feedback:'Review incident response scenarios and practice concise explanations.'}
];
export const skills = ['Technical Skills','Communication','Problem Solving','Attitude & Fit'];
export const dateLabel = iso => new Date(iso).toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'});
