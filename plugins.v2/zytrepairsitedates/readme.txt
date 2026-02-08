
1.修复站点数据,连续修复缺失 7 天的 2.清理 message表 3.sitestatistic 中已经删除的站点 
4.siteuserdata 中站点改名后将旧域名改到新域名,如果已经有这天数据就删除 hh mao rousi xingkong


sql 将siteuserdata表中 domain 中hhanclub.top 替换为hhanclub.net
UPDATE siteuserdata
SET domain = 'hhanclub.net'
WHERE domain = 'hhanclub.top';

删除 domain=rousi.zip 的所有数据
DELETE FROM siteuserdata
WHERE domain = 'rousi.zip';
