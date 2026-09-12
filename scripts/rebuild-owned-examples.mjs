import fs from 'node:fs';
import path from 'node:path';

const root = process.cwd();
const entries = [
  ['edge-intelligence','边缘智能：从云端到现场','边缘计算工程蓝图，讲清延迟、部署与生命周期。','technical','Engineering Blueprint','#132B3A','owned-edge-intelligence/edge-intelligence_ppt169_20260704'],
  ['spatial-robotics','空间机器人系统图谱','白色实验室系统图，拆解感知、定位、规划与执行。','technical','Laboratory Systems','#2D6A73','owned-spatial-robotics/spatial-robotics_ppt169_20260704'],
  ['lakehouse-handbook','Lakehouse 工程手册','终端绿黑风的数据平台架构与治理手册。','technical','Terminal Handbook','#0C6B4F','owned-lakehouse-handbook/lakehouse-handbook_ppt169_20260704'],
  ['event-driven','事件驱动架构实践','洋红拓扑风的事件流、解耦与可观测性案例。','technical','Event Topology','#7A3EF0','owned-event-driven/event-driven-architecture_ppt169_20260704'],
  ['sea-energy-growth','海上新能源增长战略','蓝白高层咨询风，覆盖市场、能力与增长路线图。','consulting','Executive Strategy','#176B9B','owned-sea-energy-growth/sea-energy-growth_ppt169_20260704'],
  ['coffee-city-growth','城市咖啡增长地图','暖红零售分析风，拆解门店、客群与城市扩张。','consulting','Retail Analytics','#C54B3C','owned-coffee-growth/coffee-city-growth_ppt169_20260705'],
  ['factory-energy','工厂能源转型路线图','工业运营风的能耗诊断、改造优先级与实施节奏。','consulting','Industrial Operations','#52606D','owned-factory-energy/factory-energy-roadmap_ppt169_20260705'],
  ['county-tourism','县域文旅焕新策略','青绿朱红的文化策略提案，连接资源、产品与运营。','consulting','Cultural Strategy','#1D7A73','owned-county-tourism/county-tourism-renewal_ppt169_20260705'],
  ['product-review','2026 产品团队年度复盘','企业复盘风，包含 KPI、留存、失败经验与路线图。','general','Product Review','#3159D9','owned-product-review/product-team-review_ppt169_20260706'],
  ['ai-camp','新员工 AI 协作训练营','模块化教学卡与工作坊任务，覆盖协作与安全边界。','general','Learning Workshop','#246BFD','owned-ai-camp/ai-collaboration-camp_ppt169_20260706'],
  ['aurora-launch','Aurora 智能耳机发布会','黑色舞台与冰蓝产品光的科技新品发布会。','creative','Product Keynote','#20C7E8','owned-aurora-launch/aurora-headphones-launch_ppt169_20260706'],
  ['island-hotel','海岛酒店改造项目汇报','建筑蓝图式改造提案，包含总图、剖面与分期投资。','consulting','Architecture Brief','#164B63','owned-island-hotel/island-hotel-renovation_ppt169_20260706'],
  ['neon-tide','NEON TIDE 城市音乐节','酸性霓虹海报语言的阵容、舞台、时刻表与售票方案。','creative','Acid Festival','#FF2BB5','owned-neon-tide/neon-tide-city-music-festival_ppt169_20260706'],
  ['bookstore-seasons','一间独立书店的四季','拼贴杂志式书店经营日志，记录街区关系与四季节奏。','editorial','Collage Zine','#7B4058','owned-bookstore-seasons/independent-bookstore-seasons_ppt169_20260706'],
  ['shanhai-scent','山海香气实验室','东方矿物香氛档案，以石、盐、木、雾建立材料谱。','creative','Mineral Fragrance','#64877C','owned-shanhai-scent-lab/shanhai-scent-lab_ppt169_20260706'],
  ['sneaker-materials','未来运动鞋材料档案','工业材料样本册，比较泡棉、菌丝、针织与模块外底。','technical','Material Archive','#145DA0','owned-future-sneaker-materials/future-sneaker-materials-archive_ppt169_20260706'],
  ['timber-revival','木构建筑的当代复兴','建筑杂志版式，连接传统木作、工程木与循环建造。','editorial','Architecture Magazine','#20342C','owned-timber-revival/timber-architecture-revival_ppt169_20260706'],
  ['night-economy','中国夜间经济观察','数据新闻风，分析时间曲线、交通边界与夜间治理。','editorial','Data Journalism','#22D3A7','owned-night-economy/china-night-economy_ppt169_20260706'],
  ['cocoa-climate','可可、气候与一块巧克力','长篇新闻叙事，追踪气候风险如何穿过可可供应链。','editorial','Longform Report','#4B2230','owned-cocoa-climate/cocoa-climate-chocolate_ppt169_20260706'],
  ['county-market','被重新发明的县城菜市场','人文编辑风，观察空间更新、摊贩生计与公共生活。','editorial','Humanist Editorial','#176B63','owned-county-market/reinvented-county-market_ppt169_20260706'],
  ['living-trends-2027','2027 居住方式趋势报告','高端趋势年鉴，讨论弹性空间、健康、科技与街区服务。','general','Trend Yearbook','#243B53','owned-living-trends-2027/living-trends-2027_ppt169_20260706']
];

const projects = entries.map(([id,title,description,style,styleName,color,projectPath]) => {
  const finalDir = path.join(root, 'examples', projectPath, 'svg_final');
  const files = fs.readdirSync(finalDir).filter(file => file.endsWith('.svg')).sort((a,b) => a.localeCompare(b, 'en', {numeric:true}));
  if (!files.length) throw new Error(`No SVG slides: ${projectPath}`);
  return {
    id, title, description, icon: '', color, style, styleName,
    desc: description,
    tags: ['PPT Master', `${files.length} slides`],
    isNew: true,
    folder: `${projectPath}/svg_final`,
    cover: files[0],
    slides: files.map((file, index) => ({
      file,
      title: index === 0 ? '封面' : `第 ${index + 1} 页`,
      desc: title
    }))
  };
});

const data = {
  version: 2,
  updated: '2026-07-07',
  stats: {
    examples: projects.length,
    pages: projects.reduce((sum, project) => sum + project.slides.length, 0),
    templates: projects.length
  },
  projects
};

const pretty = `${JSON.stringify(data, null, 2)}\n`;
fs.writeFileSync(path.join(root, 'examples/examples.json'), pretty);

const inline = JSON.stringify(data);
for (const file of ['index.html', 'viewer.html']) {
  const filePath = path.join(root, file);
  const source = fs.readFileSync(filePath, 'utf8');
  if (!source.includes('const INLINE_EXAMPLES_DATA = ')) {
    throw new Error(`INLINE_EXAMPLES_DATA marker missing in ${file}`);
  }
  const next = source.replace(
    /const INLINE_EXAMPLES_DATA = \{.*?\};/s,
    `const INLINE_EXAMPLES_DATA = ${inline};`
  );
  fs.writeFileSync(filePath, next);
}

console.log(`Rebuilt ${projects.length} examples / ${data.stats.pages} pages`);
