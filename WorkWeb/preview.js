const query = new URLSearchParams(location.search);
const direction = query.get("direction");
const compare = query.has("comparison") || direction === "b" || direction === "c";
const destination = new URL(compare ? "./comparison/" : "./ui/", location.href);
if (!compare) query.delete("direction");
destination.search = query.toString();
location.replace(destination);
